import numpy as np
import pandas as pd

from pegasusio import MultimodalData, UnimodalData
from pandas.api.types import is_categorical_dtype, is_numeric_dtype
from typing import Union, Optional, List


def set_bulk_value(col):
    if is_numeric_dtype(col):
        return col.mean()
    else:
        # Categorical
        return col.value_counts().idxmax()


def get_pseudobulk_count(X, df, attr, bulk_list):
    M = []
    for bulk in bulk_list:
        df_bulk = df.loc[df[attr] == bulk]
        bulk_vec = np.sum(X[df_bulk.index, :], axis=0).A1
        M.append(bulk_vec)

    return np.array(M, dtype=np.int32)


def pseudobulk(
    data: MultimodalData,
    sample: str,
    attrs: Optional[Union[List[str], str]] = None,
    mat_key: Optional[str] = None,
    cluster: Optional[str] = None,
) -> UnimodalData:
    """Generate Pseudo-bulk count matrices.

    Parameters
    -----------
    data: ``MultimodalData`` or ``UnimodalData`` object
        Annotated data matrix with rows for cells and columns for genes.

    sample: ``str``
        Specify the cell attribute used for aggregating pseudo-bulk data.
        Key must exist in ``data.obs``.

    attrs: ``str`` or ``List[str]``, optional, default: ``None``
        Specify additional cell attributes to remain in the pseudo bulk data.
        If set, all attributes' keys must exist in ``data.obs``.
        Notice that for a categorical attribute, each pseudo-bulk's value is the one of highest frequency among its cells,
        and for a numeric attribute, each pseudo-bulk's value is the mean among its cells.

    mat_key: ``str``, optional, default: ``None``
        Specify the single-cell count matrix used for aggregating pseudo-bulk counts:
        If ``None``, use the raw count matrix in ``data``: look for ``raw.X`` key in its matrices first; if not exists, use ``X`` key.
        Otherwise, if specified, use the count matrix with key ``mat_key`` from matrices of ``data``.

    cluster: ``str``, optional, default: ``None``
        If set, additionally generate pseudo-bulk matrices per cluster specified in ``data.obs[cluster]``.

    Returns
    -------
    A UnimodalData object ``udata`` containing pseudo-bulk information:
        * It has the following count matrices:

          * ``X``: The pseudo-bulk count matrix over all cells.
          * If ``cluster`` is set, a number of pseudo-bulk count matrices of cells belonging to the clusters, respectively.
        * ``udata.obs``: It contains pseudo-bulk attributes aggregated from the corresponding single-cell attributes.
        * ``udata.var``: Gene names and Ensembl IDs are maintained.

    Update ``data``:
        * Add the returned UnimodalData object above to ``data`` with key ``<sample>-pseudobulk``, where ``<sample>`` is replaced by the actual value of ``sample`` argument.

    Examples
    --------
    >>> pg.pseudobulk(data, sample="Channel")
    """
    if mat_key is None:
        X = (
            data.get_matrix("raw.X")
            if "raw.X" in data._unidata.matrices
            else data.get_matrix("X")
        )
    else:
        X = data.get_matrix(mat_key)

    assert sample in data.obs.columns, f"Sample key '{sample}' must exist in data.obs!"

    sample_vec = (
        data.obs[sample]
        if is_categorical_dtype(data.obs[sample])
        else data.obs[sample].astype("category")
    )
    bulk_list = sample_vec.cat.categories

    df_barcode = data.obs.reset_index()

    mat_dict = {"X": get_pseudobulk_count(X, df_barcode, sample, bulk_list)}

    # Generate pseudo-bulk attributes if specified
    bulk_attr_list = []

    if attrs is not None:
        if isinstance(attrs, str):
            attrs = [attrs]
        for attr in attrs:
            assert (
                attr in data.obs.columns
            ), f"Cell attribute key '{attr}' must exist in data.obs!"

    for bulk in bulk_list:
        df_bulk = df_barcode.loc[df_barcode[sample] == bulk]
        if attrs is not None:
            bulk_attr = df_bulk[attrs].apply(set_bulk_value, axis=0)
            bulk_attr["barcodekey"] = bulk
        else:
            bulk_attr = pd.Series({"barcodekey": bulk})
        bulk_attr_list.append(bulk_attr)

    df_pseudobulk = pd.DataFrame(bulk_attr_list)

    df_feature = pd.DataFrame(index=data.var_names)
    if "featureid" in data.var.columns:
        df_feature["featureid"] = data.var["featureid"]

    if cluster is not None:
        assert (
            cluster in data.obs.columns
        ), f"Cluster key '{attr}' must exist in data.obs!"

        cluster_list = data.obs[cluster].astype("category").cat.categories
        for cls in cluster_list:
            mat_dict[f"{cluster}_{cls}.X"] = get_pseudobulk_count(
                X, df_barcode.loc[df_barcode[cluster] == cls], sample, bulk_list
            )

    udata = UnimodalData(
        barcode_metadata=df_pseudobulk,
        feature_metadata=df_feature,
        matrices=mat_dict,
        genome=sample,
        modality="pseudobulk",
        cur_matrix="X",
    )

    data.add_data(udata)

    return udata
