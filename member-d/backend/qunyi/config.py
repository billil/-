from dataclasses import dataclass, field
from pathlib import Path
import json, os, secrets
from urllib.parse import urlsplit

@dataclass
class Config:
    state: Path = field(default_factory=lambda: Path('state'))
    mode: str = 'mock'
    tokens: dict = field(default_factory=dict, repr=False)
    endpoint: str = ''
    model: str = ''
    api_key: str = field(default='', repr=False)
    timeout: float = 12.0
    deadline: float = 30.0
    max_calls: int = 3
    allow_http_loopback: bool = False

    def validate(self):
        self.state = Path(self.state)
        if self.mode not in ('mock', 'live'): raise ValueError('QUNYI_MODE must be mock or live')
        if not 1 <= self.max_calls <= 5: raise ValueError('max_calls must be 1..5')
        if not 0 < self.timeout <= 60 or not 0 < self.deadline <= 120: raise ValueError('invalid time limits')
        if self.endpoint:
            u = urlsplit(self.endpoint)
            local = self.allow_http_loopback and u.scheme == 'http' and u.hostname in ('127.0.0.1','localhost','::1')
            if (u.scheme != 'https' and not local) or not u.hostname or u.username or u.password or u.query or u.fragment:
                raise ValueError('model URL must be HTTPS without credentials/query/fragment')
        if not isinstance(self.tokens, dict) or not self.tokens: raise ValueError('tokens required')
        if any(not isinstance(k,str) or not k.isascii() or len(k)<16 or not isinstance(v,str) or not v or len(v)>100 for k,v in self.tokens.items()):
            raise ValueError('tokens must map tokens of >=16 characters to workspace names')
        return self

    @classmethod
    def load(cls):
        state = Path(os.environ.get('QUNYI_STATE','state'))
        state.mkdir(parents=True, exist_ok=True)
        raw = os.environ.get('QUNYI_TOKENS')
        if raw:
            tokens = json.loads(raw)
        else:
            path = state/'credentials.json'
            if not path.exists():
                # Each installation gets random credentials; no shared default token.
                with path.open('x',encoding='utf-8') as f:
                    os.chmod(path,0o600)
                    json.dump({secrets.token_urlsafe(32):'demo'},f)
            tokens = json.loads(path.read_text(encoding='utf-8'))
        return cls(state,os.environ.get('QUNYI_MODE','mock'),tokens,
                   os.environ.get('QUNYI_MODEL_URL',''),os.environ.get('QUNYI_MODEL',''),
                   os.environ.get('QUNYI_API_KEY',''),float(os.environ.get('QUNYI_TIMEOUT','12')),
                   float(os.environ.get('QUNYI_DEADLINE','30')),int(os.environ.get('QUNYI_MAX_CALLS','3')),
                   os.environ.get('QUNYI_ALLOW_HTTP_LOOPBACK')=='1').validate()
