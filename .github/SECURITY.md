# Security Policy

## Supported Versions
| Version | Supported |
|---|---|
| latest (`main`) | ✅ |

## Reporting a Vulnerability
Please **do not** open a public issue for security vulnerabilities. Instead,
report them privately:

- Email **edy.cu@live.com**, or
- Use GitHub's [private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability) (Security → Report a vulnerability).

You'll get an acknowledgment within 48 hours and a resolution timeline after
triage. Please give us a reasonable window to patch before public disclosure.

## Notes specific to Armsmith
- Reports are ed25519-signed and content-addressed; `armsmith verify` re-checks
  the chain. If you find a way to make `verify` pass on a tampered report, that
  is a security bug — please report it privately.
- Private signing keys live under `~/.armsmith` (or `ARMSMITH_KEY_DIR`) and must
  never be committed; `.gitignore` excludes `*.pem` and `.armsmith/`.
