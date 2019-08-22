import time
import numpy as np
import pandas as pd

from scCloud.misc import get_rep_key, X_from_rep_key

from scipy.sparse import issparse, csr_matrix
from scipy.stats import chi2
from sklearn.neighbors import NearestNeighbors
from joblib import effective_n_jobs
from typing import List, Tuple
import logging

logger = logging.getLogger('sccloud')


def calculate_nearest_neighbors(
    X: np.array,
    K: int = 100,
    n_jobs: int = -1,
    method: str = "hnsw",
    M: int = 20,
    efC: int = 200,
    efS: int = 200,
    random_state: int = 0,
    full_speed: int = False,
):
    """Calculate nearest neighbors
    X is the sample by feature matrix
    Return K -1 neighbors, the first one is the point itself and thus omitted.
    TODO: Documentation
    """

    nsample = X.shape[0]

    if nsample <= 1000:
        method = "sklearn"

    if nsample < K:
        logger.warning(
            "Warning: in calculate_nearest_neighbors, number of samples = {} < K = {}!\n Set K to {}.".format(
                nsample, K, nsample
            )
        )
        K = nsample

    n_jobs = effective_n_jobs(n_jobs)

    if method == "hnsw":
        import hnswlib

        assert not issparse(X)
        # Build hnsw index
        knn_index = hnswlib.Index(space="l2", dim=X.shape[1])
        knn_index.init_index(
            max_elements=nsample, ef_construction=efC, M=M, random_seed=random_state
        )
        knn_index.set_num_threads(n_jobs if full_speed else 1)
        knn_index.add_items(X)

        # KNN query
        knn_index.set_ef(efS)
        knn_index.set_num_threads(n_jobs)
        indices, distances = knn_index.knn_query(X, k=K)
        # eliminate the first neighbor, which is the node itself
        for i in range(nsample):
            if indices[i, 0] != i:
                indices[i, 1:] = indices[i, 0:-1]
                distances[i, 1:] = distances[i, 0:-1]
        indices = indices[:, 1:].astype(int)
        distances = np.sqrt(distances[:, 1:])
    else:
        assert method == "sklearn"
        knn = NearestNeighbors(
            n_neighbors=K - 1, n_jobs=n_jobs
        )  # eliminate the first neighbor, which is the node itself
        knn.fit(X)
        distances, indices = knn.kneighbors()

    return indices, distances


def is_cached(data, indices_key, distances_key, K):
    return (
        (indices_key in data.uns)
        and (distances_key in data.uns) and data.uns[indices_key].shape[0] == data.shape[0]
        and (K <= data.uns[indices_key].shape[1] + 1)
    )

def get_neighbors(
    data: "AnnData",
    K: int = 100,
    rep: str = "pca",
    n_jobs: int = -1,
    random_state: int = 0,
    full_speed: bool = False,
) -> Tuple[List[int], List[float]]:
    """Find K nearest neighbors for each data point and return the indices and distances arrays.

    Parameters
    ----------

    data : `AnnData`
        An AnnData object.
    K : `int`, optional (default: 100)
        Number of neighbors, including the data point itself.
    rep : `str`, optional (default: 'pca')
        Representation used to calculate kNN. If `None` use data.X
    n_jobs : `int`, optional (default: -1)
        Number of threads to use. -1 refers to all available threads
    random_state: `int`, optional (default: 0)
        Random seed for random number generator.
    full_speed: `bool`, optional (default: False)
        If full_speed, use multiple threads in constructing hnsw index. However, the kNN results are not reproducible. If not full_speed, use only one thread to make sure results are reproducible.

    Returns
    -------

    kNN indices and distances arrays.

    Examples
    --------
    >>> indices, distances = tools.get_neighbors(adata)
    """

    rep_key, rep = get_rep_key(rep)
    indices_key = rep + "_knn_indices"
    distances_key = rep + "_knn_distances"

    if is_cached(data, indices_key, distances_key, K):
        indices = data.uns[indices_key]
        distances = data.uns[distances_key]
        logger.info("Found cached kNN results, no calculation is required.")
    else:
        indices, distances = calculate_nearest_neighbors(
            X_from_rep_key(data, rep_key),
            K=K,
            n_jobs=effective_n_jobs(n_jobs),
            random_state=random_state,
            full_speed=full_speed,
        )
        data.uns[indices_key] = indices
        data.uns[distances_key] = distances

    return indices, distances


def get_symmetric_matrix(csr_mat: "csr_matrix") -> "csr_matrix":
    tp_mat = csr_mat.transpose().tocsr()
    sym_mat = csr_mat + tp_mat
    sym_mat.sort_indices()

    idx_mat = (csr_mat != 0).astype(int) + (tp_mat != 0).astype(int)
    idx_mat.sort_indices()
    idx = idx_mat.data == 2

    sym_mat.data[idx] /= 2.0
    return sym_mat


# We should not modify distances array!
def calculate_affinity_matrix(
    indices: List[int], distances: List[float]
) -> "csr_matrix":
    start = time.time()

    nsample = indices.shape[0]
    K = indices.shape[1]
    # calculate sigma, important to use median here!
    sigmas = np.median(distances, axis=1)
    sigmas_sq = np.square(sigmas)

    # calculate local-scaled kernel
    normed_dist = np.zeros((nsample, K), dtype=float)
    for i in range(nsample):
        numers = 2.0 * sigmas[i] * sigmas[indices[i, :]]
        denoms = sigmas_sq[i] + sigmas_sq[indices[i, :]]
        normed_dist[i, :] = np.sqrt(numers / denoms) * np.exp(
            -np.square(distances[i, :]) / denoms
        )

    W = csr_matrix(
        (normed_dist.ravel(), (np.repeat(range(nsample), K), indices.ravel())),
        shape=(nsample, nsample),
    )
    W = get_symmetric_matrix(W)

    # density normalization
    z = W.sum(axis=1).A1
    W = W.tocoo()
    W.data /= z[W.row]
    W.data /= z[W.col]
    W = W.tocsr()
    W.eliminate_zeros()

    end = time.time()
    logger.info(
        "Constructing affinity matrix is done. Time spent = {:.2f}s.".format(
            end - start
        )
    )

    return W


def neighbors(
    data: "AnnData",
    K: int = 100,
    rep: "str" = "pca",
    n_jobs: int = -1,
    random_state: int = 0,
    full_speed: bool = False,
) -> None:
    """Compute k nearest neighbors and affinity matrix, which will be used for diffmap and graph-based community detection algorithms.

    Parameters
    ----------

    data : `AnnData`
        An AnnData object.
    K : `int`, optional (default: 100)
        Number of neighbors, including the data point itself.
    rep : `str`, optional (default: 'pca')
        Representation used to calculate kNN. Use `None` use data.X
    n_jobs : `int`, optional (default: -1)
        Number of threads to use. -1 refers to all available threads
    random_state: `int`, optional (default: 0)
        Random seed for random number generator.
    full_speed: `bool`, optional (default: False)
        If full_speed, use multiple threads in constructing hnsw index. However, the kNN results are not reproducible. If not full_speed, use only one thread to make sure results are reproducible.

    Returns
    -------

    kNN indices and distances arrays.

    Examples
    --------
    >>> tools.neighbors(adata)
    """

    # calculate kNN
    start = time.time()
    indices, distances = get_neighbors(
        data,
        K=K,
        rep=rep,
        n_jobs=n_jobs,
        random_state=random_state,
        full_speed=full_speed,
    )
    end = time.time()
    logger.info("Nearest neighbor search is finished in {:.2f}s.".format(end - start))

    # calculate affinity matrix
    if rep is None:
        rep = 'X'
    start = time.time()
    W = calculate_affinity_matrix(indices[:, 0 : K - 1], distances[:, 0 : K - 1])
    data.uns["W_" + rep] = W
    end = time.time()
    logger.info("Affinity matrix calculation is finished in {:.2f}s".format(end - start))


def calc_kBET_for_one_chunk(knn_indices, attr_values, ideal_dist, K):
    dof = ideal_dist.size - 1

    ns = knn_indices.shape[0]
    results = np.zeros((ns, 2))
    for i in range(ns):
        observed_counts = (
            pd.Series(attr_values[knn_indices[i, :]]).value_counts(sort=False).values
        )
        expected_counts = ideal_dist * K
        stat = np.sum(
            np.divide(
                np.square(np.subtract(observed_counts, expected_counts)),
                expected_counts,
            )
        )
        p_value = 1 - chi2.cdf(stat, dof)
        results[i, 0] = stat
        results[i, 1] = p_value

    return results


def calc_kBET(
    data: "AnnData",
    attr: str,
    rep: str = "pca",
    K: int = 25,
    alpha: float = 0.05,
    n_jobs: int = -1,
    random_state: int = 0,
    temp_folder: str = None,
) -> Tuple[float, float, float]:
    """
    TODO: Documentation
    This kBET metric is based on paper "A test metric for assessing single-cell RNA-seq batch correction" [M. Büttner, et al.] in Nature Methods, 2018.

    :return:
        stat_mean: average chi-square statistic over all the data points.
        pvalue_mean: average p-value over all the data points.
    """
    assert attr in data.obs
    if data.obs[attr].dtype.name != "category":
        data.obs[attr] = pd.Categorical(data.obs[attr])

    from joblib import Parallel, delayed

    ideal_dist = (
        data.obs[attr].value_counts(normalize=True, sort=False).values
    )  # ideal no batch effect distribution
    nsample = data.shape[0]
    nbatch = ideal_dist.size

    attr_values = data.obs[attr].values.copy()
    attr_values.categories = range(nbatch)

    indices, distances = get_neighbors(
        data, K=K, rep=rep, n_jobs=n_jobs, random_state=random_state
    )
    knn_indices = np.concatenate(
        (np.arange(nsample).reshape(-1, 1), indices[:, 0 : K - 1]), axis=1
    )  # add query as 1-nn

    # partition into chunks
    n_jobs = min(effective_n_jobs(n_jobs), nsample)
    starts = np.zeros(n_jobs + 1, dtype=int)
    quotient = nsample // n_jobs
    remainder = nsample % n_jobs
    for i in range(n_jobs):
        starts[i + 1] = starts[i] + quotient + (1 if i < remainder else 0)

    kBET_arr = np.concatenate(
        Parallel(n_jobs=n_jobs, max_nbytes=1e7, temp_folder=temp_folder)(
            delayed(calc_kBET_for_one_chunk)(
                knn_indices[starts[i] : starts[i + 1], :], attr_values, ideal_dist, K
            )
            for i in range(n_jobs)
        )
    )

    res = kBET_arr.mean(axis=0)
    stat_mean = res[0]
    pvalue_mean = res[1]
    accept_rate = (kBET_arr[:, 1] >= alpha).sum() / nsample

    return (stat_mean, pvalue_mean, accept_rate)


def calc_kSIM(
    data: "AnnData",
    attr: str,
    rep: str = "pca",
    K: int = 25,
    min_rate: float = 0.9,
    n_jobs: int = -1,
    random_state: int = 0,
) -> Tuple[float, float]:
    """
    TODO: Documentation.
    This kSIM metric measures if attr are not diffused too much
    """
    assert attr in data.obs
    nsample = data.shape[0]

    indices, distances = get_neighbors(
        data, K=K, rep=rep, n_jobs=n_jobs, random_state=random_state
    )
    knn_indices = np.concatenate(
        (np.arange(nsample).reshape(-1, 1), indices[:, 0 : K - 1]), axis=1
    )  # add query as 1-nn

    labels = np.reshape(data.obs[attr].values[knn_indices.ravel()], (-1, K))
    same_labs = labels == labels[:, 0].reshape(-1, 1)
    correct_rates = same_labs.sum(axis=1) / K

    kSIM_mean = correct_rates.mean()
    kSIM_accept_rate = (correct_rates >= min_rate).sum() / nsample

    return (kSIM_mean, kSIM_accept_rate)


# def calc_JSD(P, Q):
#     M = (P + Q) / 2
#     return (entropy(P, M, base = 2) + entropy(Q, M, base = 2)) / 2.0


# def calc_kBJSD_for_one_datapoint(pos, attr_values, knn_indices, ideal_dist):
#     idx = np.append(knn_indices[pos], [pos])
#     empirical_dist = pd.Series(attr_values[idx]).value_counts(normalize = True, sort = False).values
#     return calc_JSD(ideal_dist, empirical_dist)


# def calc_kBJSD(data, attr, rep = 'pca', K = 25, n_jobs = 1, random_state = 0, temp_folder = None):
#     assert attr in data.obs
#     if data.obs[attr].dtype.name != 'category':
#         data.obs[attr] = pd.Categorical(data.obs[attr])

#     from joblib import Parallel, delayed

#     ideal_dist = data.obs[attr].value_counts(normalize = True, sort = False).values # ideal no batch effect distribution
#     nsample = data.shape[0]
#     nbatch = ideal_dist.size

#     attr_values = data.obs[attr].values.copy()
#     attr_values.categories = range(nbatch)

#     indices, distances = get_neighbors(data, K = K, rep = rep, n_jobs = n_jobs, random_state = random_state)
#     knn_indices = indices[:, 0 : K - 1]
#     kBJSD_arr = np.array(Parallel(n_jobs = 1, max_nbytes = 1e7, temp_folder = temp_folder)(delayed(calc_kBJSD_for_one_datapoint)(i, attr_values, knn_indices, ideal_dist) for i in range(nsample)))

#     return kBJSD_arr.mean()
