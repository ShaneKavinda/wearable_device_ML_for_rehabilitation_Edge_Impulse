from __future__ import annotations

import argparse
import re
import shutil
import sys
import zipfile
from pathlib import Path


PROJECT_ID = 738400
DEPLOY_VERSION = 19
FEATURE_COUNT = 198
SAMPLE_COUNT = 33
LABEL_COUNT = 6
AXES_PER_SAMPLE = 6
INTERVAL_MS = 60.60606060606061
FREQUENCY_HZ = 16.5
FUSION_AXES = "acc_x + acc_y + acc_z + gyro_x + gyro_y + gyro_z"
LABELS = (
    "Extension",
    "Flexion",
    "Pronation",
    "Radial Deviation",
    "Supination",
    "Ulnar Deviation",
)


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(
        description="Prepare the Edge Impulse Arduino export for the Windows runner."
    )
    parser.add_argument(
        "--archive",
        type=Path,
        default=(
            repo_root
            / "resource"
            / "exported_model"
            / "ei_gesture_left_hand_imu_arduino.zip"
        ),
    )
    return parser.parse_args()


def read_zip_text(archive: zipfile.ZipFile, member: str) -> str:
    return archive.read(member).decode("utf-8")


def require_define(text: str, name: str, value: int) -> None:
    pattern = rf"^\s*#define\s+{re.escape(name)}\s+{value}\s*$"
    if re.search(pattern, text, flags=re.MULTILINE) is None:
        raise RuntimeError(f"Model metadata does not contain {name}={value}.")


def require_float_define(text: str, name: str, value: float) -> None:
    pattern = rf"^\s*#define\s+{re.escape(name)}\s+([0-9.]+)\s*$"
    match = re.search(pattern, text, flags=re.MULTILINE)
    if match is None or abs(float(match.group(1)) - value) > 1e-9:
        raise RuntimeError(f"Model metadata does not contain {name}={value}.")


def reset_output(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for child in path.iterdir():
        if child.name in {"README.md", ".gitignore"}:
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def main() -> int:
    args = parse_args()
    archive_path = args.archive.resolve()
    if not archive_path.is_file():
        raise RuntimeError(f"Model archive was not found: {archive_path}")

    repo_root = Path(__file__).resolve().parents[3]
    # Keep one short-path copy to avoid Ninja/MAX_PATH failures on Windows.
    outputs = [repo_root / ".ei_model"]

    with zipfile.ZipFile(archive_path) as archive:
        metadata_members = [
            name for name in archive.namelist()
            if name.endswith("src/model-parameters/model_metadata.h")
        ]
        if len(metadata_members) != 1:
            raise RuntimeError("Expected exactly one model_metadata.h in the archive.")

        src_prefix = metadata_members[0].split("model-parameters/model_metadata.h")[0]
        metadata = read_zip_text(archive, metadata_members[0])
        variables = read_zip_text(
            archive,
            f"{src_prefix}model-parameters/model_variables.h",
        )
        require_define(metadata, "EI_CLASSIFIER_PROJECT_ID", PROJECT_ID)
        require_define(metadata, "EI_CLASSIFIER_PROJECT_DEPLOY_VERSION", DEPLOY_VERSION)
        require_define(metadata, "EI_CLASSIFIER_RAW_SAMPLE_COUNT", SAMPLE_COUNT)
        require_define(metadata, "EI_CLASSIFIER_RAW_SAMPLES_PER_FRAME", AXES_PER_SAMPLE)
        require_define(metadata, "EI_CLASSIFIER_LABEL_COUNT", LABEL_COUNT)
        require_float_define(metadata, "EI_CLASSIFIER_FREQUENCY", FREQUENCY_HZ)
        fusion_pattern = (
            r'^\s*#define\s+EI_CLASSIFIER_FUSION_AXES_STRING\s+'
            + re.escape(f'"{FUSION_AXES}"')
            + r"\s*$"
        )
        if re.search(fusion_pattern, metadata, flags=re.MULTILINE) is None:
            raise RuntimeError("Model feature-axis names or order do not match.")
        frame_expression = re.search(
            r"^\s*#define\s+EI_CLASSIFIER_DSP_INPUT_FRAME_SIZE\s+"
            r"\(EI_CLASSIFIER_RAW_SAMPLE_COUNT\s*\*\s*"
            r"EI_CLASSIFIER_RAW_SAMPLES_PER_FRAME\)\s*$",
            metadata,
            flags=re.MULTILINE,
        )
        if frame_expression is None or SAMPLE_COUNT * AXES_PER_SAMPLE != FEATURE_COUNT:
            raise RuntimeError(f"Model input frame is not {FEATURE_COUNT} values.")
        interval = re.search(
            r"^\s*#define\s+EI_CLASSIFIER_INTERVAL_MS\s+([0-9.]+)\s*$",
            metadata,
            flags=re.MULTILINE,
        )
        if interval is None or abs(float(interval.group(1)) - INTERVAL_MS) > 1e-9:
            raise RuntimeError("Model sample interval is not 60.606 ms (16.5 Hz).")
        label_pattern = r"\{\s*" + r"\s*,\s*".join(
            re.escape(f'"{label}"') for label in LABELS
        ) + r"\s*\}"
        if re.search(label_pattern, variables) is None:
            raise RuntimeError(
                "Model labels or label order do not match the application."
            )
        raw_dsp_pattern = (
            r"ei_dsp_config_raw_t\s+\w+\s*=\s*\{"
            r".*?\b6\s*,\s*//\s*int length of axes"
            r".*?\b1\.0f\s*//\s*float scale-axes"
        )
        if re.search(raw_dsp_pattern, variables, flags=re.DOTALL) is None:
            raise RuntimeError("Model raw DSP axes or scale_axes=1.0 do not match.")

        for output in outputs:
            reset_output(output)

        extracted = 0
        for member in archive.infolist():
            if member.is_dir() or not member.filename.startswith(src_prefix):
                continue
            relative = Path(member.filename[len(src_prefix):])
            if not relative.parts or relative.parts[0] not in {
                "edge-impulse-sdk",
                "model-parameters",
                "tflite-model",
            }:
                continue
            data = archive.read(member)
            for output in outputs:
                destination = output / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
            extracted += 1

    if extracted == 0:
        raise RuntimeError("No Edge Impulse sources were extracted.")

    print(f"Prepared {extracted} model files from {archive_path.name}.")
    print(f"Verified project {PROJECT_ID}, deployment {DEPLOY_VERSION}, {FEATURE_COUNT} features.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)
