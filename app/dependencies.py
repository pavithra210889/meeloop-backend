from .database import get_session, Session
from fastapi import Depends
from typing import Annotated

SessionDep = Annotated[Session, Depends(get_session)]

