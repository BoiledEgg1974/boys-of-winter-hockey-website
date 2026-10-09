# Cloudflare in front of bowlhockey.com

Cloudflare speeds up **static assets** worldwide and shields the droplet. Dynamic pages still hit **159.203.6.136** (gunicorn + SQLite/MariaDB).

## 1. Add the site (one time)

1. Sign up / log in at [Cloudflare](https://dash.cloudflare.com/).
2. **Add a site** → enter `bowlhockey.com` → choose **Free** plan.
3. Cloudflare scans DNS; confirm **A** records for `@` and `www` point to **`159.203.6.136`** (orange cloud **Proxied**).
4. Copy the **two nameservers** Cloudflare shows (e.g. `ada.ns.cloudflare.com`).

## 2. Namecheap nameservers

1. Namecheap → **Domain List** → **bowlhockey.com** → **Manage**.
2. **Nameservers** → **Custom DNS** → paste Cloudflare’s two nameservers → save.
3. Propagation can take up to 24–48 hours (often much faster).

## 3. API token + automated settings (optional)

1. Copy `scripts/cloudflare.env.example` → **`scripts/cloudflare.env`** (gitignored).
2. Cloudflare → **My Profile** → **API Tokens** → create token with **Edit zone DNS** + **Zone Settings** for `bowlhockey.com`.
3. Run:

```powershell
python scripts/apply_cloudflare_bowl.py --dry-run
python scripts/apply_cloudflare_bowl.py
```

This sets **SSL Full**, **Always Use HTTPS**, proxied **A** records for `@` and `www`, and **Cache Rules** (cache `/static/` for 7 days; bypass `/login` and `/api/`).

You can still add or tweak rules under **Caching** → **Cache Rules** in the dashboard.

Do **not** cache HTML for `/bowl-fantasy/` etc. unless you know the pages are fully public and stale HTML is OK.

## 4. Origin nginx (real client IP)

After traffic is orange-cloud proxied, install the snippet on the droplet so logs and Flask see visitor IPs, not Cloudflare’s:

```bash
scp deploy/vps/nginx-bowl-snippets-cloudflare.conf root@159.203.6.136:/etc/nginx/snippets/bowl-cloudflare.conf
ssh root@159.203.6.136 "grep -q bowl-cloudflare /etc/nginx/sites-enabled/* || sed -i '/bowl-static/a\\    include /etc/nginx/snippets/bowl-cloudflare.conf;' /etc/nginx/sites-enabled/bowl* && nginx -t && systemctl reload nginx"
```

## 5. Origin certificate

Your droplet already uses **Let’s Encrypt** on nginx. **SSL/TLS** mode **Full (strict)** is correct once the origin cert is valid (it is today).

## 6. Verify

```bash
curl -I https://www.bowlhockey.com/bowl-fantasy/static/css/site.css
```

Look for `cf-cache-status: HIT` on repeat requests (after the cache rule exists).
