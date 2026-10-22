# Ad blocker module

Blocks ads, trackers and known malware sites for every device on your home Wi-Fi, including phones, TVs and game consoles. It runs [AdGuard Home](https://github.com/AdguardTeam/AdGuardHome) (free and open source) on a Raspberry Pi. All the settings for a home live in one file, `config/home.yaml`, so the same code works in any house.

## What you need

- Any Raspberry Pi 3, 4, 5 or Zero 2 W running Raspberry Pi OS (Bookworm or newer). Raspberry Pi OS Lite is enough.
- A network cable to the router is best. Wi-Fi works too.
- Access to your router's admin page, so you can point its DNS at the Pi.

## Set it up

1. **Get the code onto the Pi** and install PyYAML:
   ```
   sudo apt install -y git python3-yaml
   git clone https://github.com/GooseReaper/Home-Security.git
   cd Home-Security
   ```
2. **Fill in your home's settings.** Copy the template, then paste the `adblock:` block from `adblock.example.yaml` under `modules:` if it isn't there already:
   ```
   cp config/home.example.yaml config/home.yaml
   nano config/home.yaml
   ```
   You only *have* to fill in `router_ip` and `pi_ip`. Leave `admin_password` blank so you're asked for it during install, which keeps it out of the file.
3. **Check the config:**
   ```
   PYTHONPATH=src python3 -m homecontrol.modules.adblock check
   ```
4. **Preview, then install:**
   ```
   PYTHONPATH=src python3 -m homecontrol.modules.adblock install --dry-run
   sudo PYTHONPATH=src python3 -m homecontrol.modules.adblock install
   ```
5. **Do the router steps it prints.** First reserve the Pi's address, then set the router's DNS server to the Pi and remove any second DNS server.
6. **Check it's working:**
   ```
   ADBLOCK_ADMIN_PASSWORD='your-password' PYTHONPATH=src python3 -m homecontrol.modules.adblock status
   ```
   Or open the dashboard at `http://<pi_ip>`.

You can run `install` again at any time. It keeps what's already there and applies whatever you changed in `home.yaml`, such as new block lists or allowlist entries.

## Keeping private info private

- `config/home.yaml` holds your real network details. It is gitignored and must never be committed. Only the blank template is committed.
- Don't put the password in `home.yaml`. Instead, type it when prompted, or set it in the `ADBLOCK_ADMIN_PASSWORD` environment variable.

## For the control app

`status(settings, home)` and `install(settings, home)` take the `modules.adblock` and `home` sections as plain dicts. Each returns a dict with `ok` and `summary`. `status()` only makes two short HTTP calls to the Pi, so it is safe to call often from any machine on the network. `install()` must run on the Pi as root.

## If something breaks

- **A site won't load:** add its domain to `allowlist` and run `install` again, or turn off protection for a minute from the dashboard.
- **The internet stops working entirely:** set the router's DNS back to automatic. Every device goes back to normal within a few minutes.
