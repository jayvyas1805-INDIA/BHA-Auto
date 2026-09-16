import json
import os


def save_json(data, output):

    os.makedirs(
        os.path.dirname(output),
        exist_ok=True
    )

    with open(
        output,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data,
            f,
            indent=4,
            ensure_ascii=False
        )

    print(
        f"Saved → {output}"
    )