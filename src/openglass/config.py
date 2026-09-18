from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    inventory_path: str = "devices.yaml"
    config_path: str = "openglass.yaml"
    nodes_dir: str = "nodes"
    device_timeout: float = 15.0
    default_command_timeout: float = 30.0
    log_level: str = "INFO"
    debug: bool = False


def get_settings() -> Settings:
    return Settings()


settings = get_settings()