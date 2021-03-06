import torch
import torch.nn as nn
import torch.nn.functional as F

#----------------------------------------------------------------------------
# Equalized learning rate.
# reference: https://github.com/akanimax/pro_gan_pytorch/blob/master/pro_gan_pytorch/CustomLayers.py

class EqualizedConv2d(nn.Module):
    def __init__(self, in_features, out_features, kernel_size, stride, padding, bias=True):
        super(EqualizedConv2d, self).__init__()
        self.bias = bias
        self.conv = nn.Conv2d(in_features, out_features, kernel_size, stride, padding, bias=False)
        nn.init.kaiming_normal_(self.conv.weight, a=nn.init.calculate_gain('conv2d'))
        if bias:
            self.bias_param = nn.Parameter(torch.FloatTensor(out_features).fill_(0))
        self.scale = self.conv.weight.data.detach().pow(2.).mean().sqrt().item()
        self.conv.weight.data.copy_(self.conv.weight.data.div(self.scale))
    def forward(self, x):
        x = x.mul(self.scale)
        x = self.conv(x)
        if self.bias:
            return x + self.bias_param.view(1, -1, 1, 1).expand_as(x)
        return x

class EqualizedDeconv2d(nn.Module):
    def __init__(self, in_features, out_features, kernel_size, stride, padding, bias=True):
        super(EqualizedDeconv2d, self).__init__()
        self.bias = bias
        self.deconv = nn.ConvTranspose2d(in_features, out_features, kernel_size, stride, padding, bias=False)
        nn.init.kaiming_normal_(self.deconv.weight, a=nn.init.calculate_gain('conv2d'))
        if bias:
            self.bias_param = nn.Parameter(torch.FloatTensor(out_features).fill_(0))
        self.scale = self.deconv.weight.data.detach().pow(2.).mean().sqrt().item()
        self.deconv.weight.data.copy_(self.deconv.weight.data.div(self.scale))
    def forward(self, x):
        x = x.mul(self.scale)
        x = self.deconv(x)
        if self.bias:
            return x + self.bias_param.view(1, -1, 1, 1).expand_as(x)
        return x

class EqualizedLinear(nn.Module):
    def __init__(self, in_features, out_features, bias=True):
        super(EqualizedLinear, self).__init__()
        self.bias = bias
        self.linear = nn.Linear(in_features, out_features, bias=False)
        nn.init.kaiming_normal_(self.linear.weight, a=nn.init.calculate_gain('linear'))
        if bias:
            self.bias_param = nn.Parameter(torch.FloatTensor(out_features).fill_(0))
        self.scale = self.linear.weight.data.detach().pow(2.).mean().sqrt().item()
        self.linear.weight.data.copy_(self.linear.weight.data.div(self.scale))
    def forward(self, x):
        x = x.mul(self.scale)
        x = self.linear(x.view(x.size(0),-1))
        if self.bias:
            return x + self.bias_param.view(1, -1).expand_as(x)
        return x

#----------------------------------------------------------------------------
# Minibatch standard deviation.
# reference: https://github.com/tkarras/progressive_growing_of_gans/blob/master/networks.py#L127

class MinibatchStddev(nn.Module):
    def __init__(self, group_size=4):
        super(MinibatchStddev, self).__init__()
        self.group_size = group_size
    def forward(self, x):
        G = min(self.group_size, x.size(0)) if (x.size(0) % self.group_size == 0) else x.size(0)
        M = int(x.size(0) / G)
        y = torch.reshape(x, (G, M, x.size(1), x.size(2), x.size(3)))     # [GMCHW] Split minibatch into M groups of size G.
        y = y - torch.mean(y, dim=0, keepdim=True)                        # [GMCHW] Subtract mean over group.
        y = torch.mean(y.pow(2.), dim=0, keepdim=False)                   # [MCHW]  Calc variance over group.
        y = torch.sqrt(y + 1e-8)                                          # [MCHW]  Calc stddev over group.
        y = torch.mean(y.view(M,-1), dim=1, keepdim=False).view(M,1,1,1)  # [M111]  Take average over fmaps and pixels.
        y = y.repeat(G,1,x.size(2), x.size(3))                            # [N1HW]  Replicate over group and pixels.
        return torch.cat([x, y], 1)                                       # [NCHW]  Append as new fmap.

#----------------------------------------------------------------------------
# Pixelwise feature vector normalization.
# reference: https://github.com/tkarras/progressive_growing_of_gans/blob/master/networks.py#L120

class PixelwiseNorm(nn.Module):
    def __init__(self):
        super(PixelwiseNorm, self).__init__()
    def forward(self, x):
        y = torch.mean(x.pow(2.), dim=1, keepdim=True) + 1e-8 # [N1HW]
        return x.div(y.sqrt())

#----------------------------------------------------------------------------
# Smoothly fade in the new layers.

class ConcatTable(nn.Module):
    def __init__(self, layer1, layer2):
        super(ConcatTable, self).__init__()
        self.layer1 = layer1
        self.layer2 = layer2
    def forward(self,x):
        return [self.layer1(x), self.layer2(x)]

class Fadein(nn.Module):
    def __init__(self, alpha=0.):
        super(Fadein, self).__init__()
        self.alpha = alpha
    def update_alpha(self, delta):
        self.alpha = self.alpha + delta
        self.alpha = max(0, min(self.alpha, 1.0))
    def get_alpha(self):
        return self.alpha
    def forward(self, x):
        # x is a ConcatTable, with x[0] being old layer, x[1] being the new layer to be faded in
        return x[0].mul(1.0-self.alpha) + x[1].mul(self.alpha)

#----------------------------------------------------------------------------
# Nearest-neighbor upsample
# define this myself because torch.nn.Upsample has been deprecated

class Upsample(nn.Module):
    def __init__(self):
        super(Upsample, self).__init__()
    def forward(self, x):
        return F.interpolate(x, scale_factor=2, mode='nearest')
