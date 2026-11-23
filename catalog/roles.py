"""Roles are read from a protected table, never from editable user_metadata."""
from . import cloud

def get_role(request):
    if hasattr(request,'cinescope_role'): return request.cinescope_role
    role='user'
    if request.cloud_user:
        rows=cloud.api('GET','/rest/v1/user_roles',token=request.cloud_token,
            params={'select':'role','user_id':'eq.'+request.cloud_user['id'],'limit':'1'})
        if isinstance(rows,list) and rows and rows[0].get('role')=='admin': role='admin'
    request.cinescope_role=role
    return role
