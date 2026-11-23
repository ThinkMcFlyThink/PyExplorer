"""
Virtual file systems.

Every place you can browse is a ``FileSystem`` (see base.py). The rest of
the app never calls ``os.listdir`` or ``paramiko`` directly - it asks the
``VfsRegistry`` for the FileSystem that owns a path and uses the common
interface. To support a new kind of location (WebDAV, S3, zip files...)
write one new FileSystem class and register its URI scheme.
"""
