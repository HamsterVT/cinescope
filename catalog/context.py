from .cloud import configured
from . import cloud
from .roles import get_role

def site(request):
    user=getattr(request, 'cloud_user', None)
    try: role=get_role(request) if user else 'user'
    except cloud.CloudError: role='user'
    from .telegram import linked, ready
    metadata=(user or {}).get('user_metadata',{})
    return {'site_name':'CineScope','cloud_user':user,'user_role':role,
            'display_name':str(metadata.get('display_name') or metadata.get('name') or 'Киноман')[:60],
            'auth_ready':configured(),'telegram_ready':ready(),'telegram_linked':linked(user)}
