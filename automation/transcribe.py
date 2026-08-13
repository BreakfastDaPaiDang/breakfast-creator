from __future__ import annotations

import argparse
import json
from pathlib import Path

from faster_whisper import WhisperModel


def srt_timestamp(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    whole_seconds, milliseconds = divmod(milliseconds, 1_000)
    return f"{hours:02}:{minutes:02}:{whole_seconds:02},{milliseconds:03}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Transcribe audio/video to SRT and JSON locally.")
    parser.add_argument("input", type=Path, help="Input audio or video file")
    parser.add_argument("--output", type=Path, help="SRT output path; defaults next to input")
    parser.add_argument("--model", default="small", help="faster-whisper model name or path")
    parser.add_argument("--language", default=None, help="Optional language code, for example zh or en")
    parser.add_argument("--device", default="cpu", choices=["cpu", "cuda", "auto"])
    parser.add_argument("--compute-type", default="int8", help="For example int8, float16, int8_float16")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source = args.input.resolve()
    if not source.is_file():
        raise SystemExit(f"Input file not found: {source}")

    output = (args.output or source.with_suffix(".srt")).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    model = WhisperModel(args.model, device=args.device, compute_type=args.compute_type)
    segments, info = model.transcribe(
        str(source),
        language=args.language,
        vad_filter=True,
        word_timestamps=True,
    )

    records: list[dict[str, object]] = []
    srt_blocks: list[str] = []
    for index, segment in enumerate(segments, start=1):
        text = segment.text.strip()
        srt_blocks.append(
            f"{index}\n{srt_timestamp(segment.start)} --> {srt_timestamp(segment.end)}\n{text}\n"
        )
        records.append(
            {
                "index": index,
                "start": segment.start,
                "end": segment.end,
                "text": text,
                "words": [
                    {"start": word.start, "end": word.end, "word": word.word}
                    for word in (segment.words or [])
                ],
            }
        )

    output.write_text("\n".join(srt_blocks), encoding="utf-8-sig")
    output.with_suffix(".json").write_text(
        json.dumps(
            {
                "input": str(source),
                "language": info.language,
                "language_probability": info.language_probability,
                "segments": records,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(output)


if __name__ == "__main__":
    main()

