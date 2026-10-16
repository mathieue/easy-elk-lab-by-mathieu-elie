"""Available datasets, by name (used in DATAGEN_DATASETS)."""

from datasets.apache import Apache
from datasets.firewall import Firewall
from datasets.metrics import Metrics
from datasets.postgresql import Postgresql

DATASETS = {
    Apache.name: Apache,
    Firewall.name: Firewall,
    Metrics.name: Metrics,
    Postgresql.name: Postgresql,
}
