"""Privacy-preserving diagnostics for trusted QQ robot-add events.

These booleans are not proof of identity and must never grant privileges.
"""
def group_add_capabilities(payload):
    if not isinstance(payload, dict):
        return {'group_identifier_present': False, 'operator_identifier_present': False}
    def present(key):
        v = payload.get(key)
        return isinstance(v, str) and bool(v.strip()) and len(v) <= 256
    return {'group_identifier_present': present('group_openid'),
            'operator_identifier_present': present('op_member_openid')}
