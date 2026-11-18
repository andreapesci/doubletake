package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"

	"doubletake/internal/daemon"
	"doubletake/internal/daemon/daemonclient"
)

func main() {
	fs := flag.NewFlagSet(os.Args[0], flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	socketPath := fs.String("socket", daemon.DefaultSocketPath(), "daemon socket path")
	credentialStdin := fs.Bool("credential-stdin", false, "read the connect credential verbatim from stdin")
	fs.Usage = usage

	if err := fs.Parse(os.Args[1:]); err != nil {
		os.Exit(2)
	}

	args := fs.Args()
	if len(args) < 1 {
		usage()
		os.Exit(1)
	}

	client := daemonclient.New(*socketPath)
	cmd := args[0]
	if *credentialStdin && (cmd != "connect" || len(args) != 2) {
		fmt.Fprintln(os.Stderr, "-credential-stdin requires connect <target>")
		os.Exit(1)
	}

	var resp *daemon.Response
	var err error

	switch cmd {
	case "status":
		resp, err = client.Status()
	case "discover":
		resp, err = client.Discover()
	case "devices":
		resp, err = client.Devices()
	case "connect":
		target := ""
		pin := ""
		if len(args) >= 2 {
			target = args[1]
		}
		if len(args) >= 3 {
			pin = args[2]
		}
		if *credentialStdin {
			const maxCredentialBytes = 4096
			credential, readErr := io.ReadAll(io.LimitReader(os.Stdin, maxCredentialBytes+1))
			if readErr != nil || len(credential) == 0 || len(credential) > maxCredentialBytes {
				fmt.Fprintln(os.Stderr, "unable to read a non-empty credential of at most 4096 bytes from stdin")
				os.Exit(1)
			}
			pin = string(credential)
		}
		resp, err = client.Connect(target, 0, pin)
	case "pin":
		if len(args) < 2 {
			fmt.Fprintf(os.Stderr, "Usage: doubletake-ctl pin <PIN-or-password>\n")
			os.Exit(1)
		}
		resp, err = client.Connect("", 0, args[1])
	case "disconnect":
		if len(args) >= 2 {
			resp, err = client.DisconnectTarget(args[1])
		} else {
			resp, err = client.Disconnect()
		}
	case "mute":
		if len(args) >= 2 {
			resp, err = client.MuteTarget(args[1])
		} else {
			resp, err = client.Mute()
		}
	case "unmute":
		if len(args) >= 2 {
			resp, err = client.UnmuteTarget(args[1])
		} else {
			resp, err = client.Unmute()
		}
	default:
		fmt.Fprintf(os.Stderr, "unknown command: %s\n", cmd)
		usage()
		os.Exit(1)
	}

	if err != nil {
		fmt.Fprintf(os.Stderr, "error: %v\n", err)
		os.Exit(1)
	}

	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	enc.Encode(resp)

	if !resp.OK {
		os.Exit(1)
	}
}

func usage() {
	fmt.Fprintf(os.Stderr, "Usage: doubletake-ctl [-socket path] <command> [args]\n\nCommands:\n  status                              Show daemon state and all active streams\n  discover                            Wait for the current AirPlay discovery scan\n  devices                             List cached discovered devices\n  connect [target] [PIN-or-password]  Start mirroring (to target IP, or first free device)\n  pin <PIN-or-password>               Submit pairing credentials for a waiting device\n  disconnect [target]                 Stop mirroring (all streams, or only the given IP)\n  mute [target]                       Mute mirrored audio (all streams, or only the given IP)\n  unmute [target]                     Unmute mirrored audio (all streams, or only the given IP)\n\nFlags:\n  -socket path                        Override daemon socket path (default: %s)\n  -credential-stdin                   Read the exact credential for connect <target> from stdin\n", daemon.DefaultSocketPath())
}
