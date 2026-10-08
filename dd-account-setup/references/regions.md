# Regions (Step 2)

Reference table for Step 2 in `SKILL.md`: the allowed sites and their app/signup base
URLs. The decision logic (validate `DD_SITE`, else ask the user to pick) lives in Step 2
itself.

## Region reference

| Region | `DD_SITE` | App / signup base URL |
|--------|-----------|-----------------------|
| 🇺🇸 US1 — East (Virginia) | `datadoghq.com` | `https://app.datadoghq.com` |
| 🇺🇸 US3 — West (Oregon) | `us3.datadoghq.com` | `https://us3.datadoghq.com` |
| 🇺🇸 US5 — Central (Ohio) | `us5.datadoghq.com` | `https://us5.datadoghq.com` |
| 🇪🇺 EU1 — Europe (Frankfurt) | `datadoghq.eu` | `https://app.datadoghq.eu` |
| 🇯🇵 AP1 — Japan (Tokyo) | `ap1.datadoghq.com` | `https://ap1.datadoghq.com` |
| 🇦🇺 AP2 — Australia (Sydney) | `ap2.datadoghq.com` | `https://ap2.datadoghq.com` |
| 🇬🇧 UK1 — United Kingdom (London) | `uk1.datadoghq.com` | `https://uk1.datadoghq.com` |

The API host is uniformly `https://api.${DD_SITE}`.

The full allowed-site list (for validating a supplied `DD_SITE`) is exactly:

`datadoghq.com` · `us3.datadoghq.com` · `us5.datadoghq.com` · `datadoghq.eu` · `ap1.datadoghq.com` · `ap2.datadoghq.com` · `uk1.datadoghq.com`

Notes:
- Default to **US1** when the user has no preference.
- Steer the user toward the region closest to where their infrastructure runs, not where
  they are — the two often differ, and a far-away region adds latency and egress cost.
- Don't infer the region from the user's IP or location (no geolocation lookups). The
  user picks; region is permanent once an account exists.
