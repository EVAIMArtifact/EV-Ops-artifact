import torch.nn as nn

class IdentityModel(nn.Module):
    def __init__(self, configs):
        super().__init__()
        self.seq_len = configs.seq_len
    def forward(self, x):

        return x#, x