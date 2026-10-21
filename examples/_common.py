"""Command-line arguments shared by the examples."""
import argparse
import logging


def parser(description):
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--addr", default="127.0.0.1", help="switch IP address (default: %(default)s)")
    p.add_argument("--port", type=int, default=50052, help="bf_switchd gRPC port (default: %(default)s)")
    p.add_argument("--device-id", type=int, default=0, help="device ID (default: %(default)s)")
    p.add_argument("--p4-path", default=None,
                   help="directory with compiled P4 programs; with --p4-name, the program is loaded")
    p.add_argument("--p4-name", default=None,
                   help="P4 program to load (omit to use the program already running)")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return p


def setup_logging(args):
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
