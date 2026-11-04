# Security

Fix-It Lens tells people when to stop a repair, so a way past one of its gates is a safety bug, not only a security
one. Please report either privately:

- on GitHub: the repository's **Security** tab, then **Report a vulnerability**;
- or by mail to the maintainer at oscar@craftycast.com.

Please include what you did, what you expected the app to do, and what it did. Reports are answered within a week.

In scope:

- anything that clears a STOP, skips the step gate or the sentence filter (G0), or turns a gate off;
- a model call that sends what it should not (a frame with `store` set to true, a `-contributor` model id, a manual's
  text);
- household data reachable by another household, an expired or revoked token, or the public pilot door;
- a manual, plate photo, serial number, key or request body in the repo or a release.

Out of scope: the made demo replay, and anything that needs the attacker to already hold the household's token.
