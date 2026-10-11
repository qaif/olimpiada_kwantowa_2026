"""Atrapa ``micropip`` w piaskownicy serwera: instalacja pakietów nie ma tu sensu (brak sieci)."""


async def install(*args, **kwargs) -> None:
    return None
