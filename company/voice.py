"""声で指示する（Windows の音声認識を、アプリの側で使う）。

画面の音声認識（webkitSpeechRecognition）はアプリのウィンドウ（WebView2）では動かない。マイクの許可を出す口が無く、
Chromium の認識はクラウドの鍵が要るので「not-allowed」や「network」で止まる。だから認識は Python の側で行う。
Windows に入っている音声認識（System.Speech、オフラインで動く。日本語の Windows には日本語の認識器が入っている）を、
同梱している pythonnet から呼ぶ。追加の部品も模型の取得も要らない。
Windows 以外、または認識器が無いときは available=False を返し、画面は理由を出す。
"""

from __future__ import annotations

import sys
import threading
from typing import Any

_lock = threading.Lock()


def _engine_type():
    import clr  # noqa: PLC0415  # pythonnet（Windows の配布版に同梱）
    clr.AddReference("System.Speech")
    from System.Speech.Recognition import SpeechRecognitionEngine  # noqa: PLC0415
    return SpeechRecognitionEngine


def status(lang: str = "ja-JP") -> dict[str, Any]:
    """使えるかと、入っている認識器の言語。"""
    out: dict[str, Any] = {"available": False, "recognizers": [], "lang": lang, "reason": ""}
    if sys.platform != "win32":
        out["reason"] = "Windows の音声認識はこの OS にはありません"
        return out
    try:
        eng = _engine_type()
        out["recognizers"] = [str(r.Culture.Name) for r in eng.InstalledRecognizers()]
    except Exception as exc:  # noqa: BLE001  # pythonnet が無い、System.Speech が無い
        out["reason"] = f"音声認識を呼べません: {exc}"
        return out
    if lang in out["recognizers"]:
        out["available"] = True
    elif out["recognizers"]:
        out["reason"] = f"{lang} の認識器が入っていません（入っているのは {', '.join(out['recognizers'])}）。設定 → 時刻と言語 → 音声認識で日本語を足してください"
    else:
        out["reason"] = "Windows に音声認識の認識器が入っていません"
    return out


def listen(seconds: int = 15, lang: str = "ja-JP", emulate: str | None = None) -> dict[str, Any]:
    """マイクから 1 回分を聞き取って文にする。黙ると終わる。emulate は検査用（音の代わりに文を入れる）。"""
    st = status(lang)
    if not st["available"]:
        return {"text": "", "error": st["reason"]}
    try:
        from System import TimeSpan  # noqa: PLC0415
        from System.Globalization import CultureInfo  # noqa: PLC0415
        from System.Speech.Recognition import DictationGrammar  # noqa: PLC0415
        eng_type = _engine_type()
        with _lock:  # マイクは 1 つ。同時に 2 回聞かない
            eng = eng_type(CultureInfo(lang))
            try:
                eng.LoadGrammar(DictationGrammar())
                eng.InitialSilenceTimeout = TimeSpan.FromSeconds(seconds)
                eng.EndSilenceTimeout = TimeSpan.FromSeconds(1.2)
                if emulate is not None:
                    result = eng.EmulateRecognize(emulate)
                else:
                    eng.SetInputToDefaultAudioDevice()
                    result = eng.Recognize(TimeSpan.FromSeconds(seconds))
            finally:
                eng.Dispose()
        if result is None:
            return {"text": "", "error": "聞き取れませんでした（何も話されなかったか、マイクが無い）"}
        return {"text": str(result.Text), "confidence": float(result.Confidence)}
    except Exception as exc:  # noqa: BLE001
        return {"text": "", "error": f"音声認識に失敗しました: {exc}"}
