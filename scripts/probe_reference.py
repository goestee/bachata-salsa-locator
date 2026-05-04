"""One-off helper: see if the reference Lovable site exposes an API
(Supabase or REST) we can call directly to compare event lists.
"""
import re
import sys

import requests

URL = "https://latin-dance-guide.lovable.app/"


def main() -> int:
    r = requests.get(URL, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    print(f"status={r.status_code}  size={len(r.text)}")

    js_bundles = re.findall(r'src="([^"]+\.js)"', r.text)
    css_bundles = re.findall(r'href="([^"]+\.css)"', r.text)
    print("js bundles :", js_bundles)
    print("css bundles:", css_bundles)

    hints = re.findall(
        r"(supabase\.co/[^\s\"']+|VITE_[A-Z_]+|/api/[^\s\"']+|/rest/v1/[^\s\"']+)",
        r.text,
    )
    print("hints in HTML:", set(hints))

    # If we found a bundle, peek inside it for hardcoded URLs / table names.
    if js_bundles:
        for b in js_bundles[:3]:
            full = b if b.startswith("http") else f"https://latin-dance-guide.lovable.app{b}"
            try:
                br = requests.get(full, timeout=20)
            except Exception as e:   # noqa: BLE001
                print(f"   !! could not load {full}: {e}")
                continue
            print(f"\n--- bundle {full}  ({len(br.text)} bytes)")
            sup = re.findall(r"https://[a-z0-9]+\.supabase\.co[^\"'\s]*", br.text)
            tables = re.findall(r"from\(['\"]([a-z_]+)['\"]\)", br.text)
            apikeys = re.findall(r"eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+", br.text)
            print("  supabase urls:", set(sup))
            print("  table names :", set(tables))
            print("  jwt-ish     :", [k[:30] + "..." for k in set(apikeys)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
