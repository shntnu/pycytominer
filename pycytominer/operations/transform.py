"""Transform observation variables by specified groups.

References
----------
.. [1] Kessy et al. 2016 "Optimal Whitening and Decorrelation" arXiv: https://arxiv.org/abs/1512.00809
"""

import os
import numpy as np
import pandas as pd
from scipy.linalg import eigh
from scipy.stats import median_abs_deviation
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.preprocessing import StandardScaler


class Spherize(BaseEstimator, TransformerMixin):
    """Class to apply a sphering transform (aka whitening) data in the base sklearn
    transform API. Note, this implementation is modified/inspired from the following
    sources:
    1) A custom function written by Juan C. Caicedo
    2) A custom ZCA function at https://github.com/mwv/zca
    3) Notes from Niranj Chandrasekaran (https://github.com/cytomining/pycytominer/issues/90)
    4) The R package "whitening" written by Strimmer et al (http://strimmerlab.org/software/whitening/)
    5) Kessy et al. 2016 "Optimal Whitening and Decorrelation" [1]_

    Attributes
    ----------
    epsilon : float
        fudge factor parameter
    center : bool
        option to center the input X matrix
    method : str
        a string indicating which class of sphering to perform
    """

    def __init__(self, epsilon=1e-6, center=True, method="ZCA", return_numpy=False):
        """
        Parameters
        ----------
        epsilon : float, default 1e-6
            fudge factor parameter
        center : bool, default True
            option to center the input X matrix
        method : str, default "ZCA"
            a string indicating which class of sphering to perform
        return_numpy: bool, default False
            option to return ndarray, instead of dataframe
        """
        avail_methods = ["PCA", "ZCA", "PCA-cor", "ZCA-cor"]

        self.epsilon = epsilon
        self.center = center
        self.return_numpy = return_numpy

        if method not in avail_methods:
            raise ValueError(
                f"Error {method} not supported. Select one of {avail_methods}"
            )
        self.method = method

        # PCA-cor and ZCA-cor require center=True
        if self.method in ["PCA-cor", "ZCA-cor"] and not self.center:
            raise ValueError("PCA-cor and ZCA-cor require center=True")

    def fit(self, X, y=None):
        """Identify the sphering transform given self.X

        Parameters
        ----------
        X : pandas.core.frame.DataFrame
            dataframe to fit sphering transform

        Returns
        -------
        self
            With computed weights attribute
        """
        # Get Numpy representation of the DataFrame
        X = X.values

        if self.method in ["PCA-cor", "ZCA-cor"]:
            # The projection matrix for PCA-cor and ZCA-cor is the same as the
            # projection matrix for PCA and ZCA, respectively, on the standardized
            # data. So, we first standardize the data, then compute the projection

            self.standard_scaler = StandardScaler().fit(X)
            variances = self.standard_scaler.var_
            if np.any(variances == 0):
                raise ValueError(
                    "Divide by zero error, make sure low variance columns are removed"
                )

            X = self.standard_scaler.transform(X)
        else:
            if self.center:
                self.mean_centerer = StandardScaler(with_mean=True, with_std=False).fit(
                    X
                )
                X = self.mean_centerer.transform(X)

        # Get the number of observations and variables
        n, d = X.shape

        # compute the rank of the matrix X
        r = np.linalg.matrix_rank(X)

        # TODO: Below, we check for r == n-1 (or r == d). But we could also have r == n if X is not centered.
        # So PCA or ZCA without centering should end up with r == n
        if (r != n - 1) & (r != d):
            raise ValueError(
                "Sphering is not supported when the data matrix X is not full rank. Check for linear dependencies in the data and remove them."
            )

        # Get the eigenvalues and eigenvectors of the covariance matrix using SVD
        _, Sigma, Vt = np.linalg.svd(X, full_matrices=True)

        # if n <= d then Sigma has shape (n,) so it will need to be expanded to
        # d filled with the value r'th element of Sigma
        if n <= d:
            assert Sigma.shape[0] == n, "Unexpected shape of Sigma"
            assert r == n - 1, "Unexpected rank"
            Sigma = np.concatenate((Sigma[0:r], np.repeat(Sigma[r - 1], d - r)))

        Sigma = Sigma + self.epsilon

        self.W = (Vt / Sigma[:, np.newaxis]).transpose() * np.sqrt(n - 1)

        # If ZCA, perform additional rotation
        if self.method in ["ZCA", "ZCA-cor"]:
            # If rank is not d then ZCA is not possible
            # TODO: Explain this better
            assert r == d, "ZCA is not possible if rank is not d"
            self.W = self.W @ Vt

        # number of columns of self.W should be equal to that of X
        assert (
            self.W.shape[1] == X.shape[1]
        ), f"Error: W has {self.W.shape[1]} columns, X has {X.shape[1]} columns"

        return self

    def transform(self, X, y=None):
        """Perform the sphering transform

        Parameters
        ----------
        X : pd.core.frame.DataFrame
            Profile dataframe to be transformed using the precompiled weights
        y : None
            Has no effect; only used for consistency in sklearn transform API

        Returns
        -------
        pandas.core.frame.DataFrame
            Spherized dataframe
        """

        columns = X.columns

        # Get Numpy representation of the DataFrame
        X = X.values

        if self.method in ["PCA-cor", "ZCA-cor"]:
            X = self.standard_scaler.transform(X)
        else:
            if self.center:
                X = self.mean_centerer.transform(X)

        if self.method in ["PCA", "PCA-cor"]:
            columns = ["PC" + str(i) for i in range(1, X.shape[1] + 1)]

        XW = X @ self.W

        if self.return_numpy:
            return XW
        else:
            return pd.DataFrame(XW, columns=columns)


class RobustMAD(BaseEstimator, TransformerMixin):
    """Class to perform a "Robust" normalization with respect to median and mad

        scaled = (x - median) / mad

    Attributes
    ----------
    epsilon : float
        fudge factor parameter
    """

    def __init__(self, epsilon=1e-18):
        self.epsilon = epsilon

    def fit(self, X, y=None):
        """Compute the median and mad to be used for later scaling.

        Parameters
        ----------
        X : pandas.core.frame.DataFrame
            dataframe to fit RobustMAD transform

        Returns
        -------
        self
            With computed median and mad attributes
        """
        # Get the mean of the features (columns) and center if specified
        self.median = X.median()
        # The scale param is required to preserve previous behavior. More info at:
        # https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.median_absolute_deviation.html#scipy.stats.median_absolute_deviation
        self.mad = pd.Series(
            median_abs_deviation(X, nan_policy="omit", scale=1 / 1.4826),
            index=self.median.index,
        )
        return self

    def transform(self, X, copy=None):
        """Apply the RobustMAD calculation

        Parameters
        ----------
        X : pandas.core.frame.DataFrame
            dataframe to fit RobustMAD transform

        Returns
        -------
        pandas.core.frame.DataFrame
            RobustMAD transformed dataframe
        """
        return (X - self.median) / (self.mad + self.epsilon)
