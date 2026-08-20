# Route and action manifest

This is the release access/mutation contract. FastAPI's generated route list is
also checked by the offline release gate; the pattern rows below cover every
route family, including parameterized instances.

| Path / family | Methods | Anonymous | Normal | Admin | Contractor | Mutates | Caller / compatibility |
|---|---|---:|---:|---:|---:|---:|---|
| `/login`, `/signup`, `/contractor/invite/{token}` | GET, POST | Yes | Yes | Yes | Yes | POST | Public authentication and invite protocol; compatible |
| `/static/*`, `/manifest.webmanifest`, `/service-worker.js`, `/favicon.ico` | GET | Yes | Yes | Yes | Yes | No | Browser assets; compatible |
| `/logout` | GET | No | Yes | Yes | Yes | Session revoke | Header; compatibility GET retained |
| `/month` | GET | No | Personal + global | Personal + global | Personal only | No | Calendar; `scope=global` is server-denied for contractors |
| `/day/{date}` | GET | No | Personal + global | Personal + global | Personal only | No | Calendar links; global query denied for contractors |
| `/timesheet/{date}`, `/settings`, `/help` | GET | No | Own data | Own data | Own data | No | Personal app surfaces |
| `/settings/*` excluding Deputy OAuth | GET, POST, DELETE | No | Own data/actions | Own data/actions | Own data/actions | Unsafe methods | Settings forms/fetch; same-origin middleware applies |
| `/settings/deputy-api/connect`, `/recheck`, `/disconnect`, `/callback` | GET, POST | No | Denied | Own OAuth only | Denied | Yes except callback GET | Admin-only OAuth UI and server guards |
| `/settings/deputy-api-test` | POST | No | Denied | Allowed legacy diagnostic | Denied | Read-only external probe | Retained compatibility path pending separate token-probe removal review |
| `/settings/deputy-web-capture` | POST | No | Own credentials | Own credentials | Own credentials | Local ingest | Settings form; read-only toward Deputy |
| `/sync-now` | POST | No | Own sync | Own sync | Own sync | Local ingest | Settings form and calendar `S` fetch; mutating GET removed |
| `/sync-status` | GET | No | Own status | Own status | Own status | No | Progress polling |
| `/contractor` | GET | No | Denied | Denied | Redirect `/month` | No | Compatibility redirect |
| `/contractor/workdays/*` | POST | No | Denied | Denied | Own assigned workday | Local preference | Legacy mini-app action compatibility |
| `/admin`, `/admin/*` | GET, POST, DELETE | No | Denied centrally | Allowed | Denied centrally | Route-dependent | Admin templates/forms/fetch; endpoint guards remain defense in depth |
| `/admin/roster-days/conflicts` | GET | No | Denied | Allowed | Denied | No | Builder same-date warning fetch |
| `/admin/roster-days/*/deputy-trial*` | GET, POST | No | Denied | Allowed with own OAuth + Deputy permission | Denied | Confirmed POST may write Deputy | Existing explicit controlled workflow only |
| `/track-map/{key}` | GET | No | Yes | Yes | Yes for own-linked days | No | Day view image |

All authenticated POST/PUT/PATCH/DELETE requests are rejected when browser
origin evidence is cross-site. Missing `Origin` remains accepted for compatible
non-browser clients; endpoint-level checks remain on sensitive forms. OAuth and
public invite protocol paths are explicit exceptions through public routing.
