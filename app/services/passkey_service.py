from webauthn import (
    generate_registration_options,
    verify_registration_response,
    options_to_json,
    base64url_to_bytes,
    generate_authentication_options,
    verify_authentication_response,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    UserVerificationRequirement,
    ResidentKeyRequirement,
    AttestationConveyancePreference,
    PublicKeyCredentialCreationOptions,
    PublicKeyCredentialRequestOptions,
    RegistrationCredential,
    AuthenticationCredential,
    AuthenticatorAttachment,
)
from sqlmodel import Session, select
from app.users.models import User, UserPasskey
from app.config import settings
from pydantic import TypeAdapter

class PasskeyService:
    def __init__(self):
        self.rp_id = settings.RP_ID
        self.rp_name = settings.RP_NAME
        self.rp_origin = settings.RP_ORIGIN
        # Build list of accepted origins: web origin + Android APK key hash origin
        android_origins = [
            f"android:apk-key-hash:{h.strip()}"
            for h in settings.ANDROID_APK_KEY_HASHES.split(",")
            if h.strip()
        ]
        self.expected_origins = [self.rp_origin] + android_origins if android_origins else self.rp_origin

    def generate_registration_options(self, user: User, session: Session) -> str:
        """
        Generate options for creating a new passkey credential.
        """
        # Get existing credentials to exclude them
        existing_passkeys = session.exec(
            select(UserPasskey).where(UserPasskey.user_id == user.id)
        ).all()
        
        exclude_credentials = []
        for pk in existing_passkeys:
            exclude_credentials.append({
                "id": base64url_to_bytes(pk.credential_id),
                "type": "public-key",
                "transports": pk.transports.split(",") if pk.transports else [],
            })

        options = generate_registration_options(
            rp_id=self.rp_id,
            rp_name=self.rp_name,
            user_id=str(user.id).encode(),  # User ID must be bytes
            user_name=user.username,
            user_display_name=user.name,
            attestation=AttestationConveyancePreference.NONE,
            authenticator_selection=AuthenticatorSelectionCriteria(
                user_verification=UserVerificationRequirement.PREFERRED,
                resident_key=ResidentKeyRequirement.PREFERRED,
            ),
            # exclude_credentials=exclude_credentials, # Optional: prevent registering same key twice
        )
        
        return options_to_json(options)

    def _map_browser_to_pydantic(self, data: any) -> any:
        """
        Recursively map browser camelCase keys to Pydantic snake_case keys.
        Also decodes base64url strings to bytes for specific fields.
        """
        import re
        
        def to_snake(name):
            name = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
            return re.sub('([a-z0-9])([A-Z])', r'\1_\2', name).lower()

        if isinstance(data, dict):
            new_dict = {}
            for k, v in data.items():
                new_key = to_snake(k)
                # Specialized mappings
                if k == 'clientDataJSON': new_key = 'client_data_json'
                if k == 'authenticatorData': new_key = 'authenticator_data'
                
                val = v
                # Decode bytes fields from base64url string
                byte_fields = {'raw_id', 'client_data_json', 'attestation_object', 'authenticator_data', 'signature', 'user_handle'}
                if new_key in byte_fields and isinstance(v, str):
                    try:
                        val = base64url_to_bytes(v)
                    except Exception:
                        pass # Let validation fail if decoding fails
                
                if isinstance(val, (dict, list)):
                    new_dict[new_key] = self._map_browser_to_pydantic(val)
                else:
                    new_dict[new_key] = val
                    
            return new_dict
        elif isinstance(data, list):
            return [self._map_browser_to_pydantic(item) for item in data]
        else:
            return data

    def _b64_encode(self, data: bytes) -> str:
        """Helper to encode bytes to unpadded base64url string."""
        import base64
        return base64.urlsafe_b64encode(data).decode().rstrip("=")

    def verify_registration_response(
        self, user: User, response_json: str, challenge: str, session: Session
    ) -> UserPasskey:
        """
        Verify the response from the authenticator for registration.
        """
        try:
            import json
            data = json.loads(response_json)
            mapped_data = self._map_browser_to_pydantic(data)
            
            verification = verify_registration_response(
                credential=TypeAdapter(RegistrationCredential).validate_python(mapped_data),
                expected_challenge=base64url_to_bytes(challenge),
                expected_origin=self.expected_origins,
                expected_rp_id=self.rp_id,
            )
            
            # Save the new passkey
            new_passkey = UserPasskey(
                user_id=user.id,
                credential_id=self._b64_encode(verification.credential_id),
                public_key=self._b64_encode(verification.credential_public_key),
                sign_count=verification.sign_count,
                transports=",".join(mapped_data.get("transports", [])) if mapped_data.get("transports") else None,
                name="Passkey", 
            )

            session.add(new_passkey)
            session.commit()
            session.refresh(new_passkey)
            
            return new_passkey
            
        except Exception as e:
            raise ValueError(f"Registration verification failed: {str(e)}")

    def generate_authentication_options(self, user: User | None = None) -> str:
        """
        Generate options for authenticating with a passkey.
        """
        options = generate_authentication_options(
            rp_id=self.rp_id,
            user_verification=UserVerificationRequirement.PREFERRED,
        )
        return options_to_json(options)

    def verify_authentication_response(
        self, response_json: str, challenge: str, session: Session, user: User | None = None
    ) -> tuple[bool, int, int]:
        """
        Verify the login response.
        Returns (is_valid, user_id, sign_count).
        """
        try:
            import json
            data = json.loads(response_json)
            mapped_data = self._map_browser_to_pydantic(data)
            
            credential = TypeAdapter(AuthenticationCredential).validate_python(mapped_data)
            
            # Normalize credential_id for lookup (unpadded base64url)
            normalized_id = self._b64_encode(base64url_to_bytes(credential.id))
            
            passkey = session.exec(
                select(UserPasskey).where(UserPasskey.credential_id == normalized_id)
            ).first()
            
            if not passkey:
                raise ValueError("Passkey not found in database")
                
            import base64
            public_key_bytes = base64.urlsafe_b64decode(passkey.public_key + "==")

            verification = verify_authentication_response(
                credential=credential,
                expected_challenge=base64url_to_bytes(challenge),
                expected_origin=self.expected_origins,
                expected_rp_id=self.rp_id,
                credential_public_key=public_key_bytes,
                credential_current_sign_count=passkey.sign_count,
            )
            
            # Update sign count
            passkey.sign_count = verification.new_sign_count
            
            from datetime import datetime
            passkey.last_used_at = datetime.now()
            
            session.add(passkey)
            session.commit()
            
            return True, passkey.user_id, passkey.sign_count
            
        except Exception as e:
            raise ValueError(f"Authentication verification failed: {str(e)}")

passkey_service = PasskeyService()
