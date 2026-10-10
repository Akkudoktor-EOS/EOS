% SPDX-License-Identifier: Apache-2.0
(server-api-page)=

# Server API

```{include} /_generated/openapi.md
:start-line: 2
:relative-docs: ..
:relative-images:
```

## Dashboard redirects behind a reverse proxy

For direct access, EOS redirects to the request host with the configured
`server.eosdash_port`. IPv4, hostnames and bracketed IPv6 addresses are supported.

If a reverse proxy exposes EOSdash through HTTPS, another public port or a path
prefix, set `server.eosdash_public_url` to the externally reachable dashboard base
URL, for example `https://energy.example.com` or
`https://energy.example.com:9443/dashboard`. EOS preserves this scheme, port and
prefix for redirects and the dashboard link on error pages. Configure the proxy
to route that base URL to EOSdash; this setting does not configure the proxy or
change the dashboard's bind address.

The base URL must not include credentials, a query or a fragment. A request for
`/eosdash/health` with the second example redirects to
`https://energy.example.com:9443/dashboard/eosdash/health`. Raw
`X-Forwarded-Host` and `X-Forwarded-Proto` headers do not override the configured
URL. Without an explicit public URL, scheme handling follows the ASGI server's
trusted-proxy configuration; EOS cannot infer an external dashboard route from
forwarding headers.
