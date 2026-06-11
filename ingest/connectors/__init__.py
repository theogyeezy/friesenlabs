"""Source connectors for the ingestion plane.

Each connector implements the `Connector` ABC (see base.py): authenticate via a
vaulted (injected) secret provider, pull records since a cursor, and land raw
JSON to S3 + normalized rows to Aurora. HubSpot is the reference implementation;
gohighlevel (EXPERIMENTAL) and stripe_data (read-only revenue) mirror its shape,
and csv_import is the push-style file importer. registry.py is the one place
that knows them all (run_sync + the API list ride it).
"""
