"""Hinglish command eval for Jarvis. Metric: task success (right tool + right args).

  python eval_hinglish.py text                 # typed commands -> LLM intent (no mic needed)
  python eval_hinglish.py record [--redo]      # record your voice for each command
  python eval_hinglish.py audio --stt google-hi|google-en|whisper
"""
import argparse
import ast
import json
import time
from pathlib import Path

HERE = Path(__file__).parent
CASES = HERE / "eval" / "commands.jsonl"
AUDIO = HERE / "eval" / "audio"


def load_cases():
    return [json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_defs():
    """Pull MODEL, SYSTEM, APPS, TOOLS out of jarvis.py without importing it (it needs a mic)."""
    tree = ast.parse((HERE / "jarvis.py").read_text(encoding="utf-8"))
    ns = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id in ("MODEL", "SYSTEM", "APPS", "TOOLS"):
            exec(compile(ast.Module([node], []), "jarvis.py", "exec"), ns)
    return ns["MODEL"], ns["SYSTEM"], ns["TOOLS"]


def _has(pred_value, expected):
    options = expected if isinstance(expected, list) else [expected]
    return any(str(o).lower() in str(pred_value or "").lower() for o in options)


def score(case, pred_tool, pred_args):
    if case["tool"] is None:
        return pred_tool is None
    if pred_tool != case["tool"]:
        return False
    for k, v in case.get("args", {}).items():
        got = pred_args.get(k, False) if v is False else pred_args.get(k)
        if got != v:
            return False
    return all(_has(pred_args.get(k), v) for k, v in case.get("contains", {}).items())


def make_predictor():
    from groq import Groq

    client = Groq()
    model, system, tools = load_defs()
    gtools = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                 "parameters": t["input_schema"]}} for t in tools]

    def predict(text):
        for attempt in range(4):
            try:
                r = client.chat.completions.create(
                    model=model, max_tokens=200, temperature=0, tools=gtools,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": text}],
                )
                calls = r.choices[0].message.tool_calls
                if not calls:
                    return None, {}
                return calls[0].function.name, json.loads(calls[0].function.arguments or "{}")
            except Exception:
                time.sleep(2 ** attempt)
        return "ERROR", {}

    return predict


def make_stt(name, whisper_model, whisper_lang):
    if name.startswith("google"):
        import speech_recognition as sr

        rec = sr.Recognizer()
        lang = "hi-IN" if name == "google-hi" else "en-IN"

        def stt(path):
            with sr.AudioFile(str(path)) as src:
                audio = rec.record(src)
            try:
                return rec.recognize_google(audio, language=lang)
            except (sr.UnknownValueError, sr.RequestError):
                return ""

        return stt
    from faster_whisper import WhisperModel

    m = WhisperModel(whisper_model, device="auto", compute_type="auto")

    def stt(path):
        segs, _ = m.transcribe(str(path), language=whisper_lang, vad_filter=True)
        return " ".join(s.text.strip() for s in segs)

    return stt


def report(results, label):
    n = len(results)
    if not n:
        print("No results.")
        return
    print(f"\n{label}: {sum(r['ok'] for r in results)}/{n} = {100 * sum(r['ok'] for r in results) / n:.1f}% task success")
    for key in ("lang", "tool"):
        groups = {}
        for r in results:
            groups.setdefault(str(r[key]), []).append(r["ok"])
        print(f"  by {key}: " + ", ".join(f"{k} {100 * sum(v) / len(v):.0f}% ({len(v)})" for k, v in sorted(groups.items())))
    fails = [r for r in results if not r["ok"]]
    if fails:
        print("\nFailures:")
        for r in fails:
            heard = f" heard='{r['heard']}'" if "heard" in r else ""
            print(f"  #{r['id']} say='{r['say']}'{heard} -> {r['pred_tool']} {r['pred_args']} (want {r['tool']})")


def run(cases, get_text, label, out_name):
    predict = make_predictor()
    results = []
    for c in cases:
        heard = get_text(c)
        if heard is None:
            continue
        tool, args = predict(heard) if heard else (None if c["tool"] is None else "NO_SPEECH", {})
        r = {"id": c["id"], "lang": c["lang"], "say": c["say"], "tool": c["tool"],
             "pred_tool": tool, "pred_args": args, "ok": score(c, tool, args)}
        if heard != c["say"]:
            r["heard"] = heard
        results.append(r)
        time.sleep(0.3)
    (HERE / "eval" / out_name).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    report(results, label)


def record(cases, redo, only=()):
    import speech_recognition as sr

    AUDIO.mkdir(parents=True, exist_ok=True)
    rec = sr.Recognizer()
    rec.pause_threshold = 1.2  # tolerate short mid-sentence pauses
    rec.non_speaking_duration = 0.6
    with sr.Microphone() as mic:
        rec.adjust_for_ambient_noise(mic, duration=1)
        for c in cases:
            path = AUDIO / f"{c['id']}.wav"
            if path.exists() and not redo and c["id"] not in only:
                continue
            while True:
                ans = input(f"\n[{c['id']}/{len(cases)}] ({c['lang']}) Say: {c['say']}\n  Enter to record, q to quit: ")
                if ans.strip().lower() == "q":
                    return
                try:
                    audio = rec.listen(mic, timeout=6, phrase_time_limit=10)
                except sr.WaitTimeoutError:
                    print("  Heard nothing, try again.")
                    continue
                path.write_bytes(audio.get_wav_data(convert_rate=16000, convert_width=2))
                print("  saved")
                break


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["text", "record", "audio"])
    ap.add_argument("--redo", action="store_true")
    ap.add_argument("--ids", default="", help="re-record only these ids, e.g. 17,19,54")
    ap.add_argument("--stt", default="google-hi", choices=["google-hi", "google-en", "whisper"])
    ap.add_argument("--whisper-model", default="small")
    ap.add_argument("--whisper-lang", default=None, help="e.g. hi or en; default auto-detect")
    a = ap.parse_args()
    cases = load_cases()
    if a.mode == "record":
        record(cases, a.redo, {int(x) for x in a.ids.split(",") if x})
    elif a.mode == "text":
        run(cases, lambda c: c["say"], "TEXT (LLM intent only)", "results_text.json")
    else:
        stt = make_stt(a.stt, a.whisper_model, a.whisper_lang)
        have = [c for c in cases if (AUDIO / f"{c['id']}.wav").exists()]
        print(f"{len(have)}/{len(cases)} commands recorded")
        run(have, lambda c: stt(AUDIO / f"{c['id']}.wav"), f"AUDIO ({a.stt})", f"results_{a.stt}.json")


if __name__ == "__main__":
    main()