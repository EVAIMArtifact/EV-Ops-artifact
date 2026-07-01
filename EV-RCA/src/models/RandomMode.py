import torch
import torch.nn as nn

class RandomModel(nn.Module):

    def __init__(self, configs):
        super().__init__()
        self.seq_len = configs.seq_len

    def forward(self, x):

        y = torch.randn_like(x)

        return y#, y