"""Moduł zgodności z Qiskitem – wszystko liczy ``qclab`` (docs/tasks/QC-01.md § 2)."""

from qclab.backends import BasicSimulator


class BasicProvider:
    def get_backend(self, name: str = "basic_simulator") -> BasicSimulator:
        return BasicSimulator()

    def backends(self) -> list:
        return [BasicSimulator()]


__all__ = ["BasicProvider", "BasicSimulator"]
