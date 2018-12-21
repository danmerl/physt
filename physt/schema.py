"""Bin schemas for physt."""

import math
from collections import OrderedDict, Iterable
from typing import Tuple, Union, Optional

import numpy as np

from .engines import find_best_engine

DEFAULT_SCHEMA_NAME = "human"


class UnknownSchemaError(RuntimeError):
    pass


class UnknownBinCountAlgorithmError(RuntimeError):
    pass


class NotFittedError(RuntimeError):
    pass


class Schema:
    """Base class for all one-dimensional schemas.

    Attributes:
        - fitted [bad name - sklearn?]
        - adaptive [bad name]
        - bins
        - edges
        - mask

    Methods:
        - fit
        - apply
        - fit_and_apply
    """
    registered_schemas = OrderedDict()

    def copy(self) -> 'Schema':
        raise NotImplementedError()

    def is_fitted(self) -> bool:
        return ((getattr(self, "_edges", None) is not None)
                or (getattr(self, "_bins", None) is not None))

    @staticmethod
    def register(name: str):
        """Parametric decorator to make the schema name available in the facade constructors."""

        def _decorator(klass: type) -> type:
            Schema.registered_schemas[name] = klass
            return klass

        return _decorator

    @property
    def bins(self) -> np.ndarray:
        if hasattr(self, "_bins"):
            return self._bins
        edges = self.edges
        if edges is None:
            return None
        bins = np.asarray([edges[:-1], edges[1:]]).T
        mask = self.mask
        if mask is not None:
            bins = bins[mask]
        return bins

    @property
    def bin_sizes(self) -> np.ndarray:
        if self.bins is None:
            return None
        return self.bins[1:] - self.bins[:-1]

    @property
    def edges(self):
        return getattr(self, "_edges", None)

    @property
    def ndim(self):
        return 1

    @property
    def shape(self):
        if self.bins is not None:
            return self.bins.shape[0]
        else:
            return None

    @property
    def mask(self):
        return getattr(self, "_mask", None)

    @property
    def bins_and_mask(self) -> Tuple[np.ndarray, np.ndarray]:
        return self.bins, self.mask

    def fit(self, data):
        raise NotImplementedError()

    def fit_and_apply(self, data, weights=None,
                      dropna: bool = True) -> np.ndarray:
        # TODO: Handle data to make them 1D array ?
        engine = find_best_engine(data)
        data = engine.prepare_data(data)
        if dropna:
            data = data[~np.isnan(data)]
        self.fit(data)
        return self.apply(data, weights=weights, dropna=False)

    def apply(self, data, weights=None, dropna: bool = True) -> np.ndarray:
        engine = find_best_engine(data)
        if not self.is_fitted():
            raise NotFittedError(
                "Schema cannot be applied without fitting first.")
        numpy_result = engine.histogram(data, bins=self.edges, weight=weights)
        # numpy_result, _ = np.histogram(data, bins=self.edges, weights=weights)
        mask = self.mask
        if mask is not None:
            return numpy_result[mask].copy()
        else:
            return numpy_result


@Schema.register("static")
class StaticSchema(Schema):
    def __init__(self, *, bins=None, edges=None, mask=None):
        from .array_utils import make_bin_array, bins_to_edges_and_mask

        if bins is not None:
            if edges is not None:
                raise ValueError(
                    "Cannot specify both bins and edges at the same time.")
            bins = np.asarray(bins)
            if bins.ndim != 2:
                raise ValueError(
                    "Invalid dimension for bins: {0}, must be 2".format(
                        bins.ndim))
            if bins.shape[1] != 2:
                raise ValueError(
                    "bins must be an array of pairs, array of shape {0} given instead".
                    format(bins.shape))
            self._bins = bins.copy()
            self._edges, self._mask = bins_to_edges_and_mask(self._bins)
        elif edges is not None:
            edges = np.asarray(edges)
            if edges.ndim != 1:
                raise ValueError(
                    "Invalid dimension for edges: {0}, must be 1".format(
                        edges.ndim))
            self._edges = edges.copy()
            self._bins = make_bin_array(self._edges)
        else:
            raise ValueError("Must specify either bins or edges.")
        self._mask = mask

    def fit(self, data):
        pass  # Do nothing

    def copy(self) -> 'StaticSchema':
        return self.__class__(
            bins=self._bins, edges=self._edges, mask=self._mask)


@Schema.register("numpy")
class NumpySchema(Schema):
    """Binning schema mimicking the behaviour of numpy.histogram"""

    BIN_COUNT_ALGORITHMS = ("auto", "fd", "doane", "scott", "rice", "sturges",
                            "sqrt")

    def __init__(self, *, bins: Union[str, int] = 10, range=None):
        if isinstance(bins, str):
            if bins not in NumpySchema.BIN_COUNT_ALGORITHMS:
                raise UnknownBinCountAlgorithmError(
                    "Invalid bin count algoritm: {0}".format(bins))
        self.bin_arg = bins
        self.range = range

    @property
    def mask(self):
        return None

    def copy(self) -> "NumpySchema":
        schema = NumpySchema(bins=self.bin_arg, range=self.range)
        schema._edges = self._edges
        return schema

    @property
    def bins(self):
        if self._edges is None:
            return None
        return np.asarray([self._edges[:-1], self._edges[1:]])

    def fit(self, data):
        _, self._edges = np.histogram(
            data, bins=self.bin_arg, range=self.range)

    def fit_and_apply(self, data, weights=None,
                      dropna: bool = True) -> np.ndarray:
        data = np.asarray(data)
        if dropna:
            data = data[~np.isnan(data)]
        numpy_result, self._edges = np.histogram(
            data, bins=self.bin_arg, weights=weights, range=self.range)
        return numpy_result


@Schema.register("fixed_width")
class FixedWidthSchema(Schema):
    def __init__(self,
                 *,
                 bin_width,
                 bin_count=None,
                 bin_times_min=None,
                 bin_shift=0.0):
        self._bin_width = bin_width
        self._bin_count = bin_count
        self._bin_times_min = bin_times_min
        self._bin_shift = bin_shift

    def is_fitted(self):
        return ((self._bin_count is not None) and (self._bin_shift is not None)
                and (self._bin_times_min is not None)
                and (self._bin_width is not None))

    def fit(self, data):
        data_min, data_max = data.min(), data.max()
        if self._bin_shift is None:
            self._bin_shift = 0.0
        self._bin_times_min = math.floor(
            (data_min - self._bin_shift) / self._bin_width)
        bin_times_max = math.floor(
            (data_max - self._bin_shift) / self._bin_width) + 1
        self._bin_count = bin_times_max - self._bin_times_min

    @property
    def edges(self):
        if not self.is_fitted():
            return None
        indices = np.arange(self._bin_count + 1)
        return self._bin_width * (
            self._bin_times_min + indices) + self._bin_shift

    @property
    def shape(self):
        return (self._bin_count) if self._bin_count else None

    def copy(self) -> "FixedWidthSchema":
        return FixedWidthSchema(
            bin_width=self._bin_width,
            bin_count=self._bin_count,
            bin_times_min=self._bin_times_min,
            bin_shift=self._bin_shift,
        )


@Schema.register("integer")
class IntegerSchema(FixedWidthSchema):
    def __init__(self):
        super(IntegerSchema, self).__init__(bin_width=1, bin_shift=0.5)

    # @property
    # def bin_labels(self):
    #     return [str(i) for i in range(self.min_, self.max_ + 1)]

    # TODO: Use something like bincount?


@Schema.register("human")
class HumanSchema(FixedWidthSchema):
    def __init__(self, *, bins="auto", range=None):
        super(HumanSchema, self).__init__(bin_width=None)
        self.bin_arg = bins
        # TODO: deal with range (also in FixedWidth)

    def fit(self, data):
        # TODO: automatic
        bin_count = 20

        subscales = np.array([0.5, 1, 2, 2.5, 5, 10])

        # TODO: ideal_bin_count
        # if bin_count is None:
        #     bin_count = ideal_bin_count(data)
        min_ = data.min()
        max_ = data.max()
        bw = (max_ - min_) / bin_count

        power = np.floor(np.log10(bw)).astype(int)
        best_index = np.argmin(np.abs(np.log(subscales * (10.0**power) / bw)))

        self._bin_width = (10.0**power) * subscales[best_index]
        FixedWidthSchema.fit(self, data)


class MultiSchema:
    """Multi-dimensional collection of binning schemas.

    Note: this class can be inherited from in order to capture inter-dimensional relationships.
    """

    def __init__(self, schemas):
        self._schemas = tuple(schemas)

    def copy(self) -> "MultiSchema":
        copied_schemas = (schema.copy() for schema in self._schemas)
        return MultiSchema(copied_schemas)

    @property
    def ndim(self):
        return len(self._schemas)

    @property
    def shape(self):
        return tuple(schema.shape for schema in self._schemas)

    @property
    def edges(self):
        return tuple(schema.edges for schema in self.schemas)

    @property
    def bins(self):
        return tuple(schema.bins for schema in self._schemas)

    @property
    def schemas(self):
        return self._schemas

    def __getitem__(self, item):
        return self._schemas[item]

    def fit(self, data):
        # TODO: data size check
        for i, schema in enumerate(self.schemas):
            schema.fit(data[:, i])

    def fit_and_apply(self, data, weights=None) -> np.ndarray:
        self.fit(data)
        return self.apply(data, weights=weights)

    def apply(self, data, weights=None) -> np.ndarray:
        edges = [schema.edges for schema in self.schemas]
        masks = [schema.mask for schema in self.schemas]
        values, _ = np.histogramdd(data, bins=edges, weights=weights)

        changed = False
        for _, mask in enumerate(masks):
            if mask:
                # TODO: Apply the subselection
                pass

        if changed:
            values = values.copy()
        return values


def build_schema(kind: Union[str, type, Schema] = None,
                 bins: Optional[Union[str, int, Iterable]] = None,
                 **kwargs) -> Schema:
    """Helper method to create binning schema from various argument sets.

    If kind is None, bins are interpreted closely to the 
    way numpy does (for clarity).
    """
    if bins:
        if kind is None:
            # Automatically deduce static or numpy schema
            if isinstance(bins, (str, int)):
                kind = NumpySchema
            elif isinstance(bins, Iterable):
                kind = StaticSchema
                bins = np.asarray(bins)
                if bins.ndim == 1:
                    kwargs["edges"] = bins
                    bins = None

        # In any case, bins need to be passed if they exist
        kwargs["bins"] = bins

    if kind is None:
        kind = DEFAULT_SCHEMA_NAME

    if isinstance(kind, Schema):
        return kind
    elif isinstance(kind, type):
        return kind(**kwargs)
    elif isinstance(kind, str):
        if kind not in Schema.registered_schemas:
            raise UnknownSchemaError(
                "Unknown schema name, available are: {0}".format(", ").join(
                    Schema.registered_schemas.keys()))
        constructor = Schema.registered_schemas[kind]
        return constructor(**kwargs)
    else:
        raise ValueError("Cannot interpret {0} as schema".format(kind))


def build_multi_schema(kind: Optional[Union[Iterable, str, type, Schema]],
                       ndim: int, **kwargs) -> MultiSchema:
    if isinstance(kind, (Schema, str, type)) or kind is None:
        schemas = [kind] * ndim
        return build_multi_schema(schemas, ndim, **kwargs)
    elif isinstance(kind, Iterable):
        schemas = [build_schema(k, **kwargs) for k in kind]
        return MultiSchema(schemas=schemas)
    else:
        raise ValueError(
            "Cannot interpret {0} as schema or multischema".format(kind))
