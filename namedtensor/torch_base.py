import torch
from .torch_helpers import NamedTensor
from .utils import make_tuple
from .nn import nn
from .distributions import ndistributions
import opt_einsum as oe


class NTorch(type):
    def __getattr__(cls, name):
        if name in cls._build:

            def call(*args, **kwargs):
                assert "names" in kwargs, "Need `names` kwarg."
                names = kwargs["names"]
                del kwargs["names"]
                return cls.build(getattr(torch, name), names, *args, **kwargs)

            call.__doc__ = getattr(torch, name).__doc__
            return call
        elif name in cls._noshift:

            def call(ntensor, *args, **kwargs):
                return getattr(ntensor, name)(*args, **kwargs)

            call.__doc__ = getattr(torch, name).__doc__
            return call
        raise NotImplementedError(name)

    @classmethod
    def dot(cls, dims, *tensors):
        names = make_tuple(dims)
        args = []
        ids = {}
        seen_names = []
        for t in tensors:
            group = []
            for name in t._schema._names:
                if name not in ids:
                    ids[name] = len(ids)
                    seen_names.append(name)
                group.append(ids[name])
            args.append(t._tensor)
            args.append(group)
        keep = [n for n in seen_names if n not in names]
        for n in names:
            if n not in seen_names:
                raise RuntimeError("No dimension %s to contract along" % n)
        args.append([ids[n] for n in keep])
        return cls.tensor(oe.contract(*args, backend="torch"), keep)

    @staticmethod
    def narrow(tensor1, dim, start, end):
        name = dim
        return tensor1._new(
            tensor1._tensor.narrow(tensor1._schema.get(name), start, end)
        )

    @staticmethod
    def topk(tensor, dim, k, largest=True, sorted=True):
        old_names = tensor._schema._names
        top_k, arg_top_k = tensor._tensor.topk(
            k, dim=tensor._schema.get(dim), largest=largest, sorted=sorted
        )
        return (
            ntorch.tensor(top_k, old_names),
            ntorch.tensor(arg_top_k, old_names),
        )

    @staticmethod
    def stack(tensors, name):
        old_names = tensors[0]._schema._names
        for i in range(1, len(tensors)):
            if tensors[i]._schema._names != old_names:
                if set(tensors[i]._schema._names) != set(
                    tensors[0]._schema._names
                ):
                    raise RuntimeError(
                        "Tensors to stack don't have matching dimension names"
                    )
                tensors[i] = tensors[i]._force_order(tensors[0]._schema._names)
        to_stack = [tensor.values for tensor in tensors]
        old_names = list(old_names)
        old_names.insert(0, name)
        return ntorch.tensor(torch.stack(to_stack, dim=0), old_names)

    @staticmethod
    def cat(tensors, dim):
        "Concate a list of named tensors along dim"
        dim = tensors[0]._schema.get(dim)
        for i in range(1, len(tensors)):
            if tensors[i]._schema._names != tensors[0]._schema._names:
                if set(tensors[i]._schema._names) != set(
                    tensors[0]._schema._names
                ):
                    raise RuntimeError(
                        "Tensors to stack don't have matching dimension names"
                    )
                tensors[i] = tensors[i]._force_order(tensors[0]._schema._names)
        return tensors[0]._new(torch.cat([t.values for t in tensors], dim=dim))

    @staticmethod
    def gather(input, dim, index, index_dim):
        outdim = index_dim
        indim = dim
        index_order = [
            (n if n != indim else outdim) for n in input._schema._names
        ]
        b1 = index._force_order(index_order)
        dim = input._schema.get(indim)
        return input._new(
            input.values.gather(dim, b1.values), updates={indim: outdim}
        )

    @staticmethod
    def masked_select(input, mask, name="on"):
        order = mask._mask_broadcast_order(input)
        a1 = input._force_order(order)
        b1 = mask._force_order(order)
        return NamedTensor(a1.values.masked_select(b1.values), name)

    @staticmethod
    def masked_scatter_(input, mask, source):
        return input._setter(mask, "masked_scatter_", [source])

    @staticmethod
    def masked_fill_(input, mask, value):
        return input._setter(mask, "masked_fill_", [value])

    @staticmethod
    def _index_base(self, dim, index):
        name = dim
        new_names = []
        sizes = []
        for n in self._schema._names:
            if n == name:
                for n2 in index._schema._names:
                    new_names.append(n2)
                    sizes.append(index.size(n2))
            else:
                new_names.append(n)
                sizes.append(self.size(n))
        return new_names, sizes

    @staticmethod
    def index_select(self, dim, index):
        "Index into dimension names with the `index` named tensors."
        new_names, sizes = NTorch._index_base(self, dim, index)
        return NamedTensor(
            self._tensor.index_select(
                self._schema.get(dim), index._tensor.view(-1)
            ).view(*sizes),
            new_names,
        )

    @staticmethod
    def index_fill_(self, dim, index, val):
        "Index into dimension names with the `index` named tensors."
        self._tensor.index_fill_(
            self._schema.get(dim), index._tensor.view(-1), val
        )
        return self

    @staticmethod
    def index_copy_(self, dim, index, source):
        "Index into dimension names with the `index` named tensors."
        order = source._mask_broadcast_order(index)
        source = source._force_order(order)
        self.values.index_copy_(
            self._schema.get(dim), index.values, source.values
        )
        return self

    @staticmethod
    def nonzero(tensor, names=("elements", "inputdims")):
        """
        Returns a tensor containing the indices of all non-zero elements.

        Parameters
        ----------
        tensor: NamedTensor
        names : tuple, optional
            Names for the output dimensions
            default value: ("elements", "inputdims")
            default output shape: OrderedDict([("elements", number of non-zero elements),
                                               ("inputdims", input tensor's number of dimensions)])
        """

        indices = torch.nonzero(tensor.values)
        return NamedTensor(tensor=indices, names=names)

    @staticmethod
    def scatter_(input, dim, index, src, index_dim):
        indim = dim
        outdim = index_dim
        index_order = [
            (n if n != indim else outdim) for n in input._schema._names
        ]

        index_force = index._force_order(index_order)
        src_force = src._force_order(index_order)
        dim = input._schema.get(indim)
        input.values.scatter_(dim, index_force.values, src_force.values)

    @staticmethod
    def build(init, names, *args, **kwargs):
        tensor = init(*args, **kwargs)
        return NamedTensor(tensor, names)
    
    @staticmethod
    def chunk(tensor, chunks, dim):
        old_names = tensor._schema._names
        chunks = tensor._tensor.chunk(chunks, dim=tensor._schema.get(dim))
        return [ntorch.tensor(chunk, old_names) for chunk in chunks]

    @staticmethod
    def tensor(*args, **kwargs):
        if isinstance(args[0], torch.Tensor):
            return NamedTensor(*args, **kwargs)
        else:
            return NamedTensor(
                *((torch.tensor(args[0]),) + args[1:]), **kwargs
            )

    @classmethod
    def __dir__(cls):
        return set(cls.__dict__.keys()) | cls._build | cls._noshift

    _build = {"ones", "zeros", "randn", "empty", "rand", "randint", "arange"}

    _noshift = {
        "abs",
        "acos",
        "asin",
        "atan",
        "byte",
        "ceil",
        "clamp",
        "clone",
        "contiguous",
        "cos",
        "cosh",
        "cpu",
        "cuda",
        "double",
        "exp",
        "expm1",
        "float",
        "floor",
        "fmod",
        "frac",
        "half",
        "int",
        "long",
        "log",
        "mul",
        "pow",
        "reciprical",
        "relu",
        "round",
        "rsqrt",
        "short",
        "sigmoid",
        "sign",
        "sin",
        "sinh",
        "sqrt",
        "sub",
        "to",
        "tan",
        "tanh",
        "tril",
        "triu",
        "trunc",
    }


class ntorch(metaclass=NTorch):
    nn = nn
    distributions = ndistributions
    pass
