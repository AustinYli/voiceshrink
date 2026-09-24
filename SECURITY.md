# Security policy

## Supported version

Security fixes are applied to the latest released version of VoiceShrink.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting feature for this repository. Do not open a public issue for a vulnerability that could expose credentials, execute unintended commands, or disclose private audio or target output.

Include the affected version, impact, reproduction steps, and any suggested mitigation. Remove secrets and personal data from every attachment.

## Trust boundary

VoiceShrink regression bundles and scenarios are code-adjacent inputs. Replaying one can execute its configured Python callable or command, or send audio to its configured HTTP endpoint. Only replay bundles from sources you trust and inspect target settings before running them.
