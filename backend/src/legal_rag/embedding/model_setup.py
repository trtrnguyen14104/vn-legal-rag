import hashlib
import json

import httpx


def download_vncorenlp(folder):
    """Only download assets needed for wseg, atomically; avoid wget and cwd side effects."""
    assets = ["VnCoreNLP-1.2.jar", "models/wordsegmenter/vi-vocab", "models/wordsegmenter/wordsegmenter.rdr"]
    manifest = {}
    for asset in assets:
        target = folder / asset
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            temporary = target.with_name(target.name + ".part")
            with httpx.Client(timeout=120, follow_redirects=True) as client:
                with client.stream(
                    "GET", "https://raw.githubusercontent.com/vncorenlp/VnCoreNLP/master/" + asset
                ) as response:
                    response.raise_for_status()
                    with temporary.open("wb") as output:
                        for block in response.iter_bytes():
                            output.write(block)
            if temporary.stat().st_size == 0:
                raise RuntimeError("empty_model_asset")
            temporary.replace(target)
        manifest[asset] = hashlib.sha256(target.read_bytes()).hexdigest()
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
