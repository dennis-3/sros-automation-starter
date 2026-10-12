# Nokia SR OS License Configuration

This directory is used to store the Nokia SR OS (TiMOS) simulation license file required by Containerlab to boot `nokia_srsim` nodes.

---

## Why is `license.txt` Ignored?

Actual license keys issued by Nokia are proprietary and confidential. To adhere to licensing agreements and prevent accidental exposure in public version control repositories, `license/license.txt` is untracked and excluded in `.gitignore`.

---

## How to Configure Your License

1. **Obtain a License:**
   Obtain a valid simulation license for Nokia SR OS (e.g., TiMOS 25.x) from your Nokia account team, partner portal, or internal license server.

2. **Place the File:**
   Copy your license file into this directory and name it `license.txt`:

   ```bash
   cp /path/to/your/timos_license.txt license/license.txt
   ```

3. **Verify Format:**
   Ensure the file contains the standard TiMOS single-line license key string:

   ```text
   00000000-0000-0000-0000-000000000000 <BASE64_KEY> # NOKIA BELL NV(TiMOS-[BC]-25.*)
   ```

---

## Topology Integration

In [`topology.yaml`](../topology.yaml), the license is declared centrally under `topology.kinds.nokia_srsim`:

```yaml
topology:
  defaults:
    kind: nokia_srsim

  kinds:
    nokia_srsim:
      image: "${SROS_IMAGE:-localhost/nokia/srsim:25.7.R1}"
      type: sr-1
      license: "${SROS_LICENSE:-license/license.txt}"

  nodes:
    r1:
      mgmt-ipv4: 172.20.20.9
    r2:
      mgmt-ipv4: 172.20.20.2
```

When you execute `make lab-up` (or `clab deploy -t topology.yaml`), Containerlab will automatically mount `license/license.txt` into each simulated router instance.
