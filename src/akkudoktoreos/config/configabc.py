"""Abstract and base classes for configuration."""

from typing import Any, ClassVar, Optional

from akkudoktoreos.core.pydantic import PydanticBaseModel


class SettingsBaseModel(PydanticBaseModel):
    """Base model class for all settings configurations.

    The model resolves secrets when accessing fields. Fields with values starting with `!secret`
    will be dynamically resolved from a secret source.

    Note:
        Settings property names shall be disjunctive to all existing settings' property names.
    """

    # ---------------
    # Secrets
    # ---------------

    _secrets: ClassVar[Optional[dict]] = None

    @classmethod
    def _resolve_secret(cls, secret_key: str) -> str:
        """Resolves a secret key to its actual value.

        Args:
            secret_key (str): The key to resolve.

        Returns:
            str: The resolved secret value.

        Raises:
            KeyError: If the secret key is not found.
        """
        if cls._secrets is None:
            raise KeyError("Secrets not set up!")
        if secret_key not in cls._secrets:
            raise KeyError(f"Secret '{secret_key}' not found!")
        return cls._secrets[secret_key]

    def secret(self, name: str) -> Any:
        """Get attribute with secrets dynamically resolved.

        Secrets will be resolved for fields with values starting with `!secret`.

        Args:
            name (str): The name of the attribute to access.

        Returns:
            Any: The resolved value for the attribute or its raw value if not a secret.

        Raises:
            AttributeError: If the attribute does not exist in the model.
        """
        if name in list(self.model_fields.keys()):
            value = self.__dict__.get(name, None)
            if isinstance(value, str) and value.startswith("!secret"):
                secret_key = value[len("!secret") :].strip()
                return self._resolve_secret(secret_key)
            return value
        raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")

    def to_secret_dict(self) -> dict:
        """Convert this instance to a dictionary representation, with secrets dynamically resolved.

        Returns:
            dict: A dictionary where the keys are the field names of the PydanticBaseModel,
                and the values are the corresponding field values.
        """
        resolved = {}
        for name, value in self.__dict__.items():
            if isinstance(value, str) and value.startswith("!secret"):
                secret_key = value[len("!secret") :].strip()
                resolved[name] = self._resolve_secret(secret_key)
            else:
                resolved[name] = value
        return resolved
