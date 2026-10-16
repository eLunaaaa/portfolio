import argparse
import json
import sys

from app.services.video_merger import (
    MergeError,
    build_merge_plan,
    merge_event,
)


def build_argument_parser() -> argparse.ArgumentParser:
    """
    터미널에서 사용할 명령행 옵션을 정의함.

    Returns:
        설정된 ArgumentParser 객체를 반환함.

    이 함수에서 옵션을 추가하면 터미널 명령의 기능을
    확장할 수 있음.
    """

    parser = argparse.ArgumentParser(
        description=(
            "같은 사고의 차량 영상을 "
            "Timestamp 기준으로 병합함."
        )
    )

    parser.add_argument(
        "event_id",
        help="병합할 사고 ID",
    )

    parser.add_argument(
        "--mode",
        choices=(
            "common",
            "extended",
        ),
        default="extended",
        help=(
            "common은 모든 영상의 공통 구간만 사용하고, "
            "extended는 한 영상이라도 존재하는 전체 구간을 사용함. "
            "기본값은 extended임."
        ),
    )

    parser.add_argument(
        "--before",
        type=float,
        default=10.0,
        help=(
            "사고 전 최대 포함 시간임. "
            "기본값은 10초임."
        ),
    )

    parser.add_argument(
        "--after",
        type=float,
        default=20.0,
        help=(
            "사고 후 최대 포함 시간임. "
            "기본값은 20초임."
        ),
    )

    parser.add_argument(
        "--width",
        type=int,
        default=640,
        help="차량 영상 한 칸의 너비임.",
    )

    parser.add_argument(
        "--height",
        type=int,
        default=None,
        help="기본 타일 높이임. 생략하면 원본 비율에 맞춰 자동 계산함.",
    )

    parser.add_argument(
        "--fps",
        type=int,
        default=30,
        help="결과 영상의 프레임률임.",
    )

    parser.add_argument(
        "--plan-only",
        action="store_true",
        help=(
            "FFmpeg를 실행하지 않고 "
            "병합 계산 결과만 출력함."
        ),
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="기존 결과 파일을 덮어씀.",
    )

    return parser


def main() -> int:
    """
    CLI 프로그램의 시작 함수임.

    처리 순서:
        1. 터미널 인수를 읽음.
        2. --plan-only 여부를 확인함.
        3. 계획만 계산하거나 실제 병합함.
        4. 결과를 JSON 형태로 출력함.
        5. 오류 발생 시 종료 코드 1을 반환함.

    Returns:
        성공하면 0을 반환함.
        실패하면 1을 반환함.
    """

    parser = build_argument_parser()
    arguments = parser.parse_args()

    try:
        if arguments.plan_only:
            result = build_merge_plan(
                event_id=arguments.event_id,
                before_seconds=arguments.before,
                after_seconds=arguments.after,
                mode=arguments.mode,
            )

        else:
            result = merge_event(
                event_id=arguments.event_id,
                before_seconds=arguments.before,
                after_seconds=arguments.after,
                mode=arguments.mode,
                tile_width=arguments.width,
                tile_height=arguments.height,
                fps=arguments.fps,
                overwrite=arguments.overwrite,
            )

    except (
        MergeError,
        RuntimeError,
    ) as error:
        print(
            f"\n병합 실패\n{error}",
            file=sys.stderr,
        )

        return 1

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        )
    )

    if result["warnings"]:
        print("\n경고")

        for warning in result["warnings"]:
            print(f"- {warning}")

    if arguments.plan_only:
        print(
            "\n병합 계획 계산 완료. "
            "아직 MP4는 생성하지 않았음."
        )

    else:
        print(
            "\n병합 완료: "
            f"{result['output']['path']}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
