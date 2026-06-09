"""Source connectors for the ingestion plane.

Each connector implements the `Connector` ABC (see base.py): authenticate via a
vaulted (injected) secret provider, pull records since a cursor, and land raw
JSON to S3 + normalized rows to Aurora. HubSpot is the reference implementation.
"""
