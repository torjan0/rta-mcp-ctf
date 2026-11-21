# MCP Agent CTF on your machine

Play ten browser challenges about how AI agents handle untrusted tools, messages,
forms and tasks. The Forensics challenges use local artifacts. In the Attack
challenges you play an attacker against a simulated organization. All companies,
accounts, secrets, mail, packages and tools are fictional and stay inside the lab.

## Start

Install Docker Engine or Docker Desktop with Compose. Allow about 2 GB of free
disk for the images and state. From this directory run:

```sh
docker compose up --build
```

Open the [scoreboard](http://127.0.0.1:8000) and register a player account with
a password of at least eight characters. Use the same name or email and password
to sign in at the [lab](http://127.0.0.1:8100). Submit flags on the scoreboard.

Both services listen on your computer's loopback address. If Docker runs on a
remote host, use a secure tunnel rather than exposing these ports to the internet.

## Pick an agent backend

The stack chooses a backend each time it starts:

1. Set `ANTHROPIC_API_KEY` in your shell for Claude Haiku 4.5 through the Anthropic API.
2. Or set `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` for an account with
   Bedrock access to Claude Haiku 4.5. `AWS_SESSION_TOKEN` is passed through for
   temporary credentials. Set `AWS_DEFAULT_REGION` if needed.
3. With neither, the lab uses a scripted stand-in. The hub clearly labels this
   practice mode. It is useful for exploring the interfaces, but its behavior
   does not predict a real model's response to an attack.

For example:

```sh
ANTHROPIC_API_KEY=your_key docker compose up --build
```

Each real model run costs a small amount. At the app's default accounting rates
for Haiku 4.5, input is about $1 and output about $5 per million tokens; a
short run is usually a fraction of a cent to a few cents. Your provider bills
your own account. The lab also limits runs per player and total estimated spend.
Provider prices can change, so check your provider before running many attempts.

The A2 and A3 repositories are simulated in memory. Write issues directly on
their lab pages. No GitHub account or token is needed.

## Reset

`docker compose down` stops the stack and keeps accounts, scores and flags.
To start a fresh game, run `docker compose down -v`, then `docker compose up --build`.
The `-v` option permanently deletes this stack's accounts, scores and flags.

All challenge effects are simulated. Do not put real credentials or personal
information into the challenge forms.
