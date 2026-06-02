from __future__ import annotations

from pathlib import Path
import shutil

from setuptools import find_packages, setup


HARNESS_ROOT = Path("packages/harness")
APP_ROOT = Path(".")
BUILD_ROOT = Path("build")
EGG_INFO_ROOT = Path("anvil_backend.egg-info")


shutil.rmtree(BUILD_ROOT, ignore_errors=True)
shutil.rmtree(EGG_INFO_ROOT, ignore_errors=True)


def build_packages() -> tuple[list[str], list[str], list[str]]:
    harness_packages = find_packages(where=str(HARNESS_ROOT))
    app_packages = find_packages(where=str(APP_ROOT), include=["app*"])
    combined = sorted(set([*harness_packages, *app_packages]))
    return harness_packages, app_packages, combined


def build_package_dir(harness_packages: list[str], app_packages: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for package in harness_packages:
        mapping[package] = str(HARNESS_ROOT / Path(package.replace(".", "/")))
    for package in app_packages:
        mapping[package] = str(Path(package.replace(".", "/")))
    return mapping


harness_packages, app_packages, all_packages = build_packages()


setup(
    packages=all_packages,
    package_dir=build_package_dir(harness_packages, app_packages),
    include_package_data=True,
)
