# Connecting a client

The server speaks MCP over stdio. A client launches it as a subprocess and
talks to it on stdin/stdout — there is no port to configure and no daemon to
keep running.

## Run it directly

```bash
python -m warnsync_mcp --corpus data/sample_corpus --poll-interval 30
```

On its own this just waits for a client to connect. It is useful for checking
that the process starts and for pointing `--corpus` at your own records.

## Configure an MCP client

Most clients read a JSON configuration with an `mcpServers` object.
[`mcp_config.json`](mcp_config.json) is a working example — copy the
`warnsync` entry into your client's configuration file, replacing the absolute
paths, and restart the client.

```json
{
  "mcpServers": {
    "warnsync": {
      "command": "python",
      "args": ["-m", "warnsync_mcp", "--corpus", "/path/to/data/sample_corpus"],
      "env": { "PYTHONPATH": "/path/to/warnsync-mcp/src" }
    }
  }
}
```

Pointing `command` at a virtualenv's `python` (or at the installed
`warnsync-mcp` console script) avoids setting `PYTHONPATH` at all.

Once connected, the five tools appear in the client's tool list. Useful things
to ask for: a search with the cited letter version, the trend counts for a CFR
provision, or the update log since a timestamp.

## Write your own client

[`../scripts/demo_live.py`](../scripts/demo_live.py) is a complete client in
about 150 lines: it spawns the server, performs the handshake, subscribes to
corpus changes, and re-queries after a change lands. It is the shortest path to
understanding the subscription flow.
