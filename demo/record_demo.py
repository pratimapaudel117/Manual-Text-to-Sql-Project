"""Record a silent demo video of the app answering every test question.

Start the app first (python app.py), then run from the project folder:
    python demo/record_demo.py

Output: demo/demo.mp4
"""

import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from test_cases import TEST_CASES  # noqa: E402

APP_URL = "http://localhost:8000"
OUT_DIR = Path(__file__).resolve().parent
SIZE = {"width": 1280, "height": 800}
EXPECTED_LABELS = {"answer": "correct result", "clarify": "ask a follow-up question", "refuse": "refuse"}

TITLE_CARD = """
<body style="margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
             font-family:system-ui,sans-serif;background:#0f172a;color:#f8fafc;text-align:center">
  <div>
    <div style="font-size:52px;font-weight:700">{title}</div>
    <div style="font-size:24px;margin-top:20px;color:#cbd5e1;line-height:1.6">{subtitle}</div>
  </div>
</body>
"""

# A caption bar pinned to the bottom of the page, since the video has no audio
CAPTION_JS = """text => {
  let bar = document.getElementById('demo-caption');
  if (!bar) {
    bar = document.createElement('div');
    bar.id = 'demo-caption';
    bar.style.cssText = 'position:fixed;left:0;right:0;bottom:0;padding:14px 24px;background:#0f172a;' +
      'color:#f8fafc;font:18px system-ui,sans-serif;z-index:9999';
    document.body.appendChild(bar);
  }
  bar.innerHTML = text;
}"""


def card(page, title, subtitle, seconds):
    page.set_content(TITLE_CARD.format(title=title, subtitle=subtitle))
    page.wait_for_timeout(seconds * 1000)


def expected_text(case):
    if case["expected_behavior"] != "answer":
        return EXPECTED_LABELS[case["expected_behavior"]]
    values = ", ".join(" | ".join(str(v) for v in row) for row in case["expected_result"])
    return f"correct result ({values})"


def record():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport=SIZE, record_video_dir=str(OUT_DIR), record_video_size=SIZE)
        page = context.new_page()

        card(page, "Ask questions to your database",
             "Plain-English questions to SQL with a local LLM<br>"
             f"Walking through all {len(TEST_CASES)} test questions", 4)

        page.goto(APP_URL)
        for i, case in enumerate(TEST_CASES, start=1):
            page.evaluate(CAPTION_JS, f"<b>Test {i} of {len(TEST_CASES)}</b> &nbsp;·&nbsp; "
                                      f"{case['category']} &nbsp;·&nbsp; expected: {expected_text(case)}")
            box = page.locator("#q")
            box.fill("")
            box.press_sequentially(case["question"], delay=25)
            page.click("#b")
            page.wait_for_function("document.getElementById('b').textContent === 'Ask'", timeout=120_000)
            page.wait_for_timeout(2500)  # time to read the answer

        page.goto(f"{APP_URL}/tests")
        page.evaluate(CAPTION_JS, "<b>Evaluation suite</b> &nbsp;·&nbsp; every question checked against "
                                  "an answer worked out by hand")
        page.wait_for_timeout(1500)
        page.click("#run")
        page.wait_for_selector("#summary.ok, #summary.bad", timeout=600_000)
        page.wait_for_timeout(4000)

        card(page, "All checks, every query",
             "Read-only database · one SELECT only · allowed tables<br>"
             "1,000-row limit · 5-second timeout · private data refused", 4)

        video = page.video.path()
        context.close()
        browser.close()
    return Path(video)


def to_mp4(webm, mp4):
    subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(webm),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "26", "-movflags", "+faststart", str(mp4)],
        check=True,
    )
    webm.unlink()


if __name__ == "__main__":
    webm = record()
    mp4 = OUT_DIR / "demo.mp4"
    to_mp4(webm, mp4)
    print(f"Saved {mp4} ({mp4.stat().st_size / 1e6:.1f} MB)")
