# Visitor stats from nginx logs (GoAccess)

Counts visitors to the public deployment by parsing nginx's own access log.
No cookies, no frontend code, no third party -- which is why this, and not
Google Analytics: GA would need a UK GDPR/PECR consent banner and a DPIA
conversation with STFC, and would mean shipping a Google tracker inside an
app whose local build's selling point is that nothing leaves the machine.

Everything here runs on the public VM (the box holding the
`workbench.goldilocks.ac.uk` certificate), not in the agent container.

## What it does and does not tell you

- **Does**: unique visitors/day, hits, bandwidth, top request paths,
  referrers, browsers, OS, response codes, and 404s.
- **Does not**: time on page, clicks, which Tool anyone opened, funnels.
  Those need frontend instrumentation -- a self-hosted cookieless
  analytics service (Plausible/Umami) is the next step up if you ever
  need them, not GA.
- **Caveat on "unique visitors"**: GoAccess derives it from IP + user
  agent + date. Everyone arriving through one STFC NAT egress looks like
  one visitor, and `anonymize-ip` zeroes the last octet before storage,
  which coarsens it further. Read the number as a trend, not a headcount.

## Setup

1. **Give each site its own log** so agent and workbench numbers stay
   separable. In `/etc/nginx/sites-enabled/`, inside each `server {}`:
   ```nginx
   access_log /var/log/nginx/agent.access.log;      # the agent's server block
   access_log /var/log/nginx/workbench.access.log;  # the workbench's
   ```
   Check what's there now first -- if a site has no `access_log` line it
   inherits `/var/log/nginx/access.log` from `nginx.conf` and both sites
   are already mixed together in it:
   ```bash
   grep -rn access_log /etc/nginx/nginx.conf /etc/nginx/sites-enabled/
   ```
   `goaccess.conf`'s `log-file` assumes `agent.access.log`; adjust if you
   name it differently.

2. **Install GoAccess and verify the version supports the on-disk DB** --
   `persist`/`restore` are what make the report survive logrotate, and a
   build without them fails loudly at startup rather than silently:
   ```bash
   sudo apt install goaccess
   goaccess --version
   ```
   Ubuntu's package has been fine for this; if `--persist` is rejected,
   use GoAccess' own apt repo (https://goaccess.io/download) instead.

3. **Install the config and units**, from this directory:
   ```bash
   sudo mkdir -p /etc/goaccess /var/lib/goaccess /var/www/goaccess
   sudo cp goaccess.conf /etc/goaccess/goaccess.conf
   sudo cp goaccess-report.service goaccess-report.timer /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now goaccess-report.timer
   sudo systemctl start goaccess-report.service   # generate one now
   ```

4. **Publish it behind a password**:
   ```bash
   sudo apt install apache2-utils
   sudo htpasswd -c /etc/nginx/.htpasswd-stats junwen   # prompts for a password
   ```
   Then `include` `nginx-stats.conf` (this directory) in the TLS
   `server {}` block certbot manages, and reload:
   ```bash
   sudo nginx -t && sudo systemctl reload nginx
   ```
   Report: `https://<the site>/stats/agent.html`.

## Backfilling existing logs

The first run only sees the current log file. To fold in what logrotate
already archived, feed the rotated files oldest-first, once, before the
timer takes over:

```bash
sudo zcat -f /var/log/nginx/agent.access.log.*.gz /var/log/nginx/agent.access.log.1 \
  | sudo goaccess - --config-file=/etc/goaccess/goaccess.conf
```

Run this more than once and those hits are counted twice -- the on-disk DB
has no idea it has seen a line before.

## Checking it works

```bash
systemctl list-timers goaccess-report.timer   # next/last run
journalctl -u goaccess-report.service -n 20   # parse errors show up here
```

A report showing zero visitors with a non-empty log almost always means
`log-format` doesn't match the site's actual `access_log` format.
