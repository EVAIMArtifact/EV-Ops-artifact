import torch.nn as nn

class MeanModel(nn.Module):
    def __init__(self, configs):
        super().__init__()
        self.seq_len = configs.seq_len
    def forward(self, x):

        mean = x.mean(dim=1, keepdim=True)

        y = mean.repeat(1, x.shape[1], 1)

        return y#, y