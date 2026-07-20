from __future__ import annotations

import argparse
import asyncio
import math
from pathlib import Path

from cloud_service.runner import FEATURE_COUNT, EdgeImpulseRunner


async def run_smoke_test(executable: Path) -> None:
    runner = EdgeImpulseRunner(executable, timeout_s=30.0)
    try:
        result = await runner.classify(1, [0.0] * FEATURE_COUNT)
    finally:
        await runner.stop()
    if len(result.scores) != 6:
        raise RuntimeError("The runner did not return six scores.")
    if any(not math.isfinite(score) for score in result.scores):
        raise RuntimeError("The runner returned a non-finite score.")
    if result.inference_us <= 0:
        raise RuntimeError("The runner did not report inference timing.")
    print(
        "Runner smoke test passed: "
        f"inference_us={result.inference_us}, score_sum={sum(result.scores):.6f}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    args = parser.parse_args()
    asyncio.run(run_smoke_test(args.executable.resolve()))


if __name__ == "__main__":
    main()

