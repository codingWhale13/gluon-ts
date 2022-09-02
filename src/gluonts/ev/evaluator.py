# Copyright 2018 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License").
# You may not use this file except in compliance with the License.
# A copy of the License is located at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# or in the "license" file accompanying this file. This file is distributed
# on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either
# express or implied. See the License for the specific language governing
# permissions and limitations under the License.


import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Dict, Collection, Iterator, List

from toolz import keyfilter

from .api import Metric
from .metrics import MSE, Mape
from ..dataset.split import TestDataset
from ..model import Forecast


def resolve_dependencies(metrics: Collection[Metric]) -> Collection[Metric]:
    # note: only considers metric.name as the only "dependency" for an aggregation is the underlying metric
    def resolve(metric):
        metrics = {}

        for dep in map(resolve, metric.dependencies):
            metrics.update(dep)

        metrics[metric.name] = metric

        return metrics

    result = {}

    for metric in map(resolve, metrics):
        result.update(metric)

    return result.values()


def topo_sort_metrics(metrics: Collection[Metric]) -> List[Metric]:
    return list(metrics)  # todo: actually sort


@dataclass
class LocalMetrics:
    data: Dict[str, np.ndarray]
    target_metrics: Collection[str]
    metrics: Collection[Metric]
    metadata: dict

    def get(self) -> Dict[str, pd.DataFrame]:
        return keyfilter(lambda name: name in self.target_metrics, self.data)

    def aggregate(self) -> dict:
        aggregations = dict()
        for metric in self.metrics:
            if metric.can_aggregate:
                aggregations[metric.aggregation_name] = metric.get_aggregate(
                    self.data[metric.name]
                )
        return aggregations


class Evaluator:
    _default_metrics = (
        MSE(),
        MSE(aggr="mean"),
        Mape(aggr="mean"),
    )

    def __init__(self, metrics: Collection[Metric] = _default_metrics) -> None:
        self.local_metric_targets = [
            metric for metric in metrics if not metric.can_aggregate
        ]
        self.aggregation_targets = [
            metric for metric in metrics if metric.can_aggregate
        ]

        required_metrics = resolve_dependencies(metrics)
        self.local_metrics = topo_sort_metrics(required_metrics)

    def apply(
        self, test_pairs: TestDataset, forecasts: Iterator[Forecast]
    ) -> LocalMetrics:
        metrics_data = {metric.name: [] for metric in self.local_metrics}
        metadata = {"item_id": [], "start": []}

        test_pairs_iter = iter(test_pairs)
        for index in range(len(test_pairs.dataset)):
            input_data, label = next(test_pairs_iter)
            metadata["item_id"].append(input_data["item_id"])
            metadata["start"].append(input_data["start"])
            forecast = next(forecasts)

            latest_metrics_data = dict()
            for metric in self.local_metrics:
                value = metric.get(
                    input_data, label, forecast, latest_metrics_data
                )
                metrics_data[metric.name].append(value)
                latest_metrics_data[metric.name] = value

        metrics_data_np = {
            key: np.stack(value, axis=0) for key, value in metrics_data.items()
        }
        return LocalMetrics(
            data=metrics_data_np,
            metrics=self.local_metrics + self.aggregation_targets,
            target_metrics=[
                metric.name for metric in self.local_metric_targets
            ],
            metadata=metadata,
        )
