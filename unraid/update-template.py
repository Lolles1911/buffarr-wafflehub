"""Add fields that are new in unraid/buffarr.xml to an existing Unraid
container template, without touching any value already filled in.

Unraid keeps a container's settings in
/boot/config/plugins/dockerMan/templates-user/my-<name>.xml, so downloading
the new template over that file would wipe them. Run this instead, from the
Unraid terminal (it runs inside the buffarr image, so nothing is installed):

    docker run --rm --entrypoint python3 \
      -v /boot/config/plugins/dockerMan/templates-user:/t \
      ghcr.io/lolles1911/buffarr-wafflehub:latest -c \
      "$(curl -fsSL https://raw.githubusercontent.com/Lolles1911/buffarr-wafflehub/main/unraid/update-template.py)"

Every my-*.xml whose Repository is this image gets a .bak copy first.
"""

import glob
import shutil
import urllib.request
import xml.etree.ElementTree as ET

TEMPLATE_URL = "https://raw.githubusercontent.com/Lolles1911/buffarr-wafflehub/main/unraid/buffarr.xml"
IMAGE = "buffarr-wafflehub"

new = ET.fromstring(urllib.request.urlopen(TEMPLATE_URL, timeout=30).read())
found = False
for path in sorted(glob.glob("/t/my-*.xml")):
    try:
        tree = ET.parse(path)
    except ET.ParseError:
        continue
    root = tree.getroot()
    if IMAGE not in (root.findtext("Repository") or ""):
        continue
    found = True
    have = {c.get("Target") for c in root.findall("Config")}
    added = []
    for c in new.findall("Config"):
        if c.get("Target") not in have:
            c.tail = "\n  "
            root.append(c)
            added.append(c.get("Target"))
    name = path.split("/")[-1]
    if added:
        shutil.copyfile(path, path + ".bak")
        tree.write(path, encoding="utf-8", xml_declaration=True)
        print(f"{name}: added {', '.join(added)} (backup: {name}.bak)")
    else:
        print(f"{name}: already up to date")

if not found:
    print("No buffarr template found in templates-user (looked for my-*.xml using the buffarr-wafflehub image).")
