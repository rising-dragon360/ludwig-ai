from typing import Optional

import numpy as np
import torch

from ludwig.utils.torch_utils import LudwigModule


# implementation adapted from https://github.com/dreamquark-ai/tabnet
class GhostBatchNormalization(LudwigModule):
    def __init__(
        self, num_features: int, momentum: float = 0.9, epsilon: float = 1e-3, virtual_batch_size: Optional[int] = None
    ):
        super().__init__()
        self.num_features = num_features
        self.virtual_batch_size = virtual_batch_size
        self.bn = torch.nn.BatchNorm1d(num_features, momentum=momentum, eps=epsilon)

    def forward(self, inputs):
        if self.training and self.virtual_batch_size:
            batch_size = inputs.shape[0]

            splits = inputs.chunk(int(np.ceil(batch_size / self.virtual_batch_size)), 0)
            x = [self.bn(x) for x in splits]
            return torch.cat(x, 0)

        return self.bn(inputs)

    @property
    def moving_mean(self) -> torch.Tensor:
        return self.bn.running_mean

    @property
    def moving_variance(self) -> torch.Tensor:
        return self.bn.running_var

    @property
    def output_shape(self) -> torch.Size:
        return torch.Size([self.num_features])

    @property
    def input_shape(self) -> torch.Size:
        return torch.Size([self.num_features])
