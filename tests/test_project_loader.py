"""Load real projects (.c3p and .zip panel dumps) and sanity-check the join map.

PROJECTS is empty in the repository: project files belong to the site they
came from. Add paths to your own .c3p/.zip to exercise the loader.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _pkg  # noqa: E402,F401

from crestron_cip.project_loader import load_project

PROJECTS: list[str] = [
]


def main():
    for path in PROJECTS:
        if not os.path.exists(path):
            print(f"  SKIP (missing): {os.path.basename(path)}")
            continue
        jm = load_project(path)
        assert len(jm) > 0, f"no joins parsed from {path}"
        print(f"\n== {os.path.basename(path)}  joins={len(jm)}  presses={len(jm.presses())}  conn={jm.connection}")
        for e in jm.labelled()[:6]:
            tag = "W" if e.is_output else "R"
            print(f"     [{tag}] join {e.join:<5} {e.signal:<6} {e.kind:<20} {e.label!r}")
    print("project loader OK (add PROJECTS paths to exercise it)")


if __name__ == "__main__":
    main()
