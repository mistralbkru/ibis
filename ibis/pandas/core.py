from __future__ import absolute_import

import collections
import numbers
import datetime

import six

import numpy as np

import toolz

import ibis.expr.types as ir
import ibis.expr.datatypes as dt

import ibis.pandas.context as ctx
from ibis.pandas.dispatch import (
    execute, execute_node, execute_first, data_preload
)


integer_types = six.integer_types + (np.integer,)
floating_types = numbers.Real,
numeric_types = integer_types + floating_types
boolean_types = bool, np.bool_
fixed_width_types = numeric_types + boolean_types
temporal_types = (
    datetime.datetime, datetime.date, datetime.timedelta, datetime.time,
    np.datetime64, np.timedelta64,
)
scalar_types = fixed_width_types + temporal_types
simple_types = scalar_types + six.string_types


def find_data(expr):
    """Find data sources bound to `expr`.

    Parameters
    ----------
    expr : ibis.expr.types.Expr

    Returns
    -------
    data : collections.OrderedDict
    """
    stack = [expr]
    seen = set()
    data = collections.OrderedDict()

    while stack:
        e = stack.pop()
        node = e.op()

        if node not in seen:
            seen.add(node)

            if hasattr(node, 'source'):
                data[node] = node.source.dictionary[node.name]
            elif isinstance(node, ir.Literal):
                data[node] = node.value

            stack.extend(
                arg for arg in reversed(node.args) if isinstance(arg, ir.Expr)
            )
    return data


_VALID_INPUT_TYPES = (ir.Expr, dt.DataType, type(None)) + scalar_types


@execute.register(ir.Expr, dict)
def execute_with_scope(expr, scope, context=None, **kwargs):
    """Execute an expression `expr`, with data provided in `scope`.

    Parameters
    ----------
    expr : ir.Expr
        The expression to execute.
    scope : dict
        A dictionary mapping :class:`~ibis.expr.types.Node` subclass instances
        to concrete data such as a pandas DataFrame.

    Returns
    -------
    result : scalar, pd.Series, pd.DataFrame
    """
    op = expr.op()

    # base case: our op has been computed (or is a leaf data node), so
    # return the corresponding value
    if op in scope:
        return scope[op]

    if context is None:
        context = ctx.Summarize()

    try:
        computed_args = [scope[t] for t in op.root_tables()]
    except KeyError:
        pass
    else:
        try:
            # special case: we have a definition of execute_first that matches
            # our current operation and data leaves
            return execute_first(
                op, *computed_args, scope=scope, context=context, **kwargs
            )
        except NotImplementedError:
            pass

    args = op.args

    # recursively compute the op's arguments
    computed_args = [
        execute(arg, scope, context=context, **kwargs)
        if hasattr(arg, 'op') else arg
        for arg in args if isinstance(arg, _VALID_INPUT_TYPES)
    ]

    # Compute our op, with its computed arguments
    return execute_node(
        op, *computed_args,
        scope=scope,
        context=context,
        **kwargs
    )


@execute.register(ir.Expr)
def execute_without_scope(
        expr, params=None, scope=None, context=None, **kwargs):
    """Execute an expression against data that are bound to it. If no data
    are bound, raise an Exception.

    Parameters
    ----------
    expr : ir.Expr
        The expression to execute
    params : Dict[Expr, object]

    Returns
    -------
    result : scalar, pd.Series, pd.DataFrame

    Raises
    ------
    ValueError
        * If no data are bound to the input expression
    """

    data_scope = find_data(expr)
    if not data_scope:
        raise ValueError(
            'No data sources found while trying to execute against the pandas '
            'backend'
        )

    factory = type(data_scope)

    if scope is None:
        scope = factory()

    if params is None:
        params = factory()

    params = {k.op() if hasattr(k, 'op') else k: v for k, v in params.items()}

    new_scope = toolz.merge(scope, data_scope, params, factory=factory)
    new_scope.update(
        (node, data_preload(node, data, scope=new_scope))
        for node, data in new_scope.items()
    )

    # By default, our aggregate functions are N -> 1
    return execute(
        expr,
        new_scope,
        context=context if context is not None else ctx.Summarize(),
        **kwargs
    )
