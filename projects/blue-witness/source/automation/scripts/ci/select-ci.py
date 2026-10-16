"""Choose affected checks and keep main's Pi deployment bundle complete."""
import json
import os

PI_COMPONENTS = ("sensor", "filter", "p2p", "ai", "time_sync", "uploader")


def select_checks(matches, event, ref):
    full = event == "workflow_dispatch"
    selected = [name for name in PI_COMPONENTS if full or name in matches]
    # A main Pi change produces all six binaries for the existing fleet deployer.
    if selected and ref == "refs/heads/main":
        selected = list(PI_COMPONENTS)
    return {
        "components": json.dumps(selected, separators=(",", ":")),
        "server": str(full or "server" in matches or "contracts" in matches).lower(),
        "contracts": str(full or "contracts" in matches).lower(),
        "aruco": str(full or "aruco" in matches).lower(),
    }


if __name__ == "__main__":
    outputs = select_checks(json.loads(os.environ["MATCHES"]),
                            os.environ["CI_EVENT"], os.environ["CI_REF"])
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        for key, value in outputs.items():
            output.write(f"{key}={value}\n")
