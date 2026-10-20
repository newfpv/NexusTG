"""Prevent sharing the same authorization key between managed live workers."""
import base64
import hashlib
import os
import struct
from pathlib import Path


def authorization_fingerprint(session_string):
    data=base64.urlsafe_b64decode(session_string+'='*(-len(session_string)%4))
    formats=('>BI?256sQ?','>B?256sQ?','>B?256sI?')
    for fmt in formats:
        if len(data)==struct.calcsize(fmt):
            values=struct.unpack(fmt,data)
            key=values[3] if fmt==formats[0] else values[2]
            return hashlib.sha256(key).hexdigest()
    raise ValueError('Invalid Telegram session format')


def claim_session(app,session_string):
    root=os.environ.get('NEXUS_RESOURCE_DIR')
    if not root or os.name=='nt':return
    import fcntl
    directory=Path(root)/'authorizations'
    directory.mkdir(parents=True,exist_ok=True)
    fd=os.open(directory/(authorization_fingerprint(session_string)+'.lock'),os.O_CREAT|os.O_RDWR,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BaseException:
        os.close(fd)
        raise RuntimeError('Telegram authorization is already running in another account profile') from None
    app._nexus_authorization_fd=fd


def release_session(app):
    fd=getattr(app,'_nexus_authorization_fd',None)
    if fd is not None:
        os.close(fd)
        app._nexus_authorization_fd=None
