# -*- coding: utf-8 -*-
"""
Quality assurance and control module
"""
import logging
import numpy as np
import os
import pandas as pd
import plotting as mplt
import plotly.express as px
from warnings import warn

from rex import Resource
from rex.utilities.execution import SpawnProcessPool

logger = logging.getLogger(__name__)


class Summarize:
    """
    reV Summary data for QA/QC
    """
    def __init__(self, h5_file, group=None):
        """
        Parameters
        ----------
        h5_file : str
            Path to .h5 file to summarize data from
        group : str, optional
            Group within h5_file to summarize datasets for, by default None
        """
        self._h5_file = h5_file
        self._group = group

    def __repr__(self):
        msg = "{} for {}".format(self.__class__.__name__, self.h5_file)

        return msg

    @property
    def h5_file(self):
        """
        .h5 file path

        Returns
        -------
        str
        """
        return self._h5_file

    @staticmethod
    def _compute_sites_summary(h5_fhandle, ds_name, sites=None):
        """
        Compute summary stats for given sites of given dataset

        Parameters
        ----------
        h5_fhandle : Resource
            Open Resource handler object
        ds_name : str
            Dataset name of interest
        sites : list | slice, optional
            sites of interest, by default None

        Returns
        -------
        sites_summary : pandas.DataFrame
            Summary stats for given sites / dataset
        """
        if sites is None:
            sites = slice(None)

        sites_meta = h5_fhandle['meta', sites]
        sites_data = h5_fhandle[ds_name, :, sites]
        sites_summary = pd.DataFrame(sites_data, columns=sites_meta.index)
        sites_summary = sites_summary.describe().T.drop(columns=['count'])
        sites_summary['sum'] = sites_data.sum(axis=0)

        return sites_summary

    @staticmethod
    def _compute_ds_summary(h5_fhandle, ds_name):
        """
        Compute summary statistics for given dataset (assumed to be a vector)

        Parameters
        ----------
        h5_fhandle : Resource
            Resource handler object
        ds_name : str
            Dataset name of interest

        Returns
        -------
        ds_summary : pandas.DataFrame
            Summary statistics for dataset
        """
        ds_data = h5_fhandle[ds_name, :]
        ds_summary = pd.DataFrame(ds_data, columns=[ds_name])
        ds_summary = ds_summary.describe().drop(['count'])
        ds_summary.at['sum'] = ds_data.sum()

        return ds_summary

    def summarize_dset(self, ds_name, process_size=None, max_workers=None,
                       out_path=None):
        """
        Compute dataset summary. If dataset is 2D compute temporal statistics
        for each site

        Parameters
        ----------
        ds_name : str
            Dataset name of interest
        process_size : int, optional
            Number of sites to process at a time, by default None
        max_workers : int, optional
            Number of workers to use in parallel, if 1 run in serial,
            if None use all available cores, by default None
        out_path : str
            File path to save summary to

        Returns
        -------
        summary : pandas.DataFrame
            Summary summary for dataset
        """
        with Resource(self.h5_file, group=self._group) as f:
            ds_shape, _, ds_chunks = f.get_dset_properties(ds_name)
            if len(ds_shape) > 1:
                sites = np.arange(ds_shape[1])
                if max_workers > 1:
                    if process_size is None:
                        process_size = ds_chunks

                    sites = \
                        np.array_split(sites,
                                       int(np.ceil(len(sites) / process_size)))
                    loggers = [__name__]
                    with SpawnProcessPool(max_workers=max_workers,
                                          loggers=loggers) as ex:
                        futures = []
                        for site_slice in sites:
                            futures.append(ex.submit(
                                self._compute_sites_summary,
                                f, ds_name, site_slice))

                        summary = [future.result() for future in futures]

                    summary = pd.concat(summary)
                else:
                    if process_size is None:
                        summary = self._compute_sites_summary(f, ds_name,
                                                              sites)
                    else:
                        sites = np.array_split(
                            sites, int(np.ceil(len(sites) / process_size)))

                        summary = []
                        for site_slice in sites:
                            summary.append(self._compute_sites_summary(
                                f, ds_name, site_slice))

                        summary = pd.concat(summary)

                summary.index.name = 'gid'
            else:
                if process_size is not None or max_workers > 1:
                    msg = ("Computing summary statistics for 1D datasets will "
                           "proceed in serial")
                    logger.warning(msg)
                    warn(msg)

                summary = self._compute_ds_summary(f, ds_name)

        if out_path is not None:
            summary.to_csv(out_path)

        return summary

    def summarize_means(self, out_path=None):
        """
        Add means datasets to meta data

        Parameters
        ----------
        out_path : str, optional
            Path to .csv file to save update meta data to, by default None

        Returns
        -------
        meta : pandas.DataFrame
            Meta data with means datasets added
        """
        with Resource(self.h5_file, group=self._group) as f:
            meta = f.meta
            if 'gid' not in meta:
                meta.index.name = 'gid'
                meta = meta.reset_index()

            for ds_name in f.datasets:
                if ds_name not in ['meta', 'time_index']:
                    shape = f.get_dset_properties(ds_name)[0]
                    if len(shape) == 1:
                        meta[ds_name] = f[ds_name]

        if out_path is not None:
            meta.to_csv(out_path, index=False)

        return meta

    @classmethod
    def run(cls, h5_file, out_dir, group=None, dsets=None,
            process_size=None, max_workers=None):
        """
        Summarize all datasets in h5_file and dump to out_dir

        Parameters
        ----------
        h5_file : str
            Path to .h5 file to summarize data from
        out_dir : str
            Directory to dump summary .csv files to
        group : str, optional
            Group within h5_file to summarize datasets for, by default None
        dsets : str | list, optional
            Datasets to summarize, by default None
        process_size : int, optional
            Number of sites to process at a time, by default None
        max_workers : int, optional
            Number of workers to use when summarizing 2D datasets,
            by default None
        """
        if not os.path.exists(out_dir):
            os.makedirs(out_dir)

        if dsets is None:
            with Resource(h5_file, group=group) as f:
                dsets = [dset for dset in f.datasets
                         if dset not in ['meta', 'time_index']]
        elif isinstance(dsets, str):
            dsets = [dsets]

        summary = cls(h5_file)
        for ds_name in dsets:
            out_path = os.path.join(out_dir,
                                    "{}_summary.csv".format(ds_name))
            summary.summarize_dset(ds_name, process_size=process_size,
                                   max_workers=max_workers, out_path=out_path)

        out_path = os.path.basename(h5_file).replace('.h5', '_summary.csv')
        out_path = os.path.join(out_dir, out_path)
        summary.summarize_means(out_path=out_path)


class SummaryPlots:
    """
    Plot summary data for QA/QC
    """
    def __init__(self, summary):
        """
        Parameters
        ----------
        summary : str | pandas.DataFrame
            Summary DataFrame or path to summary .csv
        """
        self._summary = self._parse_summary(summary)

    @property
    def summary(self):
        """
        Summary table

        Returns
        -------
        pandas.DataFrame
        """
        return self._summary

    @property
    def columns(self):
        """
        Available columns in summary table

        Returns
        -------
        list
        """
        return list(self._summary.columns)

    @staticmethod
    def _parse_summary(summary):
        """
        Extract summary statistics

        Parameters
        ----------
        summary : str | pd.DataFrame
            Path to .csv or .json or DataFrame to parse

        Returns
        -------
        summary : pandas.DataFrame
            DataFrame of summary statistics
        """
        if isinstance(summary, str):
            if summary.endswith('.csv'):
                summary = pd.read_csv(summary)
            elif summary.endswith('.json'):
                summary = pd.read_json(summary)
            else:
                raise ValueError('Cannot parse {}'.format(summary))

        elif not isinstance(summary, pd.DataFrame):
            raise ValueError("summary must be a .csv, .json, or "
                             "a pandas DataFrame")

        return summary

    @staticmethod
    def _save_plotly(fig, out_path):
        """
        Save plotly figure to disk

        Parameters
        ----------
        fig : plotly.Figure
            Plotly Figure object
        out_path : str
            File path to save plot to, can be a .html or static image
        """
        if out_path.endswith('.html'):
            fig.write_html(out_path)
        else:
            fig.write_image(out_path)

    def _check_value(self, values, scatter=True):
        """
        Check summary table for needed columns

        Parameters
        ----------
        values : str | list
            Column(s) to plot
        scatter : bool, optional
            Flag to check for latitude and longitude columns, by default True
        """
        if isinstance(values, str):
            values = [values]

        if scatter:
            values += ['latitude', 'longitude']

        for value in values:
            if value not in self.summary:
                msg = ("{} is not a valid column in summary table:\n{}"
                       .format(value, self.columns))
                logger.error(msg)
                raise ValueError(msg)

    def scatter_plot(self, value, cmap='viridis', out_path=None, **kwargs):
        """
        Plot scatter plot of value versus longitude and latitude using
        pandas.plot.scatter

        Parameters
        ----------
        value : str
            Column name to plot as color
        cmap : str, optional
            Matplotlib colormap name, by default 'viridis'
        out_path : str, optional
            File path to save plot to, by default None
        kwargs : dict
            Additional kwargs for plotting.dataframes.df_scatter
        """
        self._check_value(value)
        mplt.df_scatter(self.summary, x='longitude', y='latitude', c=value,
                        colormap=cmap, filename=out_path, **kwargs)

    def scatter_plotly(self, value, cmap='Viridis', out_path=None, **kwargs):
        """
        Plot scatter plot of value versus longitude and latitude using
        plotly

        Parameters
        ----------
        value : str
            Column name to plot as color
        cmap : str | px.color, optional
            Continuous color scale to use, by default 'Viridis'
        out_path : str, optional
            File path to save plot to, can be a .html or static image,
            by default None
        kwargs : dict
            Additional kwargs for plotly.express.scatter
        """
        self._check_value(value)
        fig = px.scatter(self.summary, x='longitude', y='latitude',
                         color=value, color_continuous_scale=cmap, **kwargs)
        fig.update_layout(font=dict(family="Arial", size=18, color="black"))

        if out_path is not None:
            self._save_plotly(fig, out_path)

        fig.show()

    def _extract_sc_data(self, lcoe='total_lcoe'):
        """
        Extract supply curve data

        Parameters
        ----------
        lcoe : str, optional
            LCOE value to use for supply curve, by default 'total_lcoe'

        Returns
        -------
        sc_df : pandas.DataFrame
            Supply curve data
        """
        values = ['capacity', lcoe]
        self._check_value(values, scatter=False)
        sc_df = self.summary[values].sort_values(lcoe)
        sc_df['cumulative_capacity'] = sc_df['capacity'].cumsum()

        return sc_df

    def supply_curve_plot(self, lcoe='total_lcoe', out_path=None, **kwargs):
        """
        Plot supply curve (cumulative capacity vs lcoe) using seaborn.scatter

        Parameters
        ----------
        lcoe : str, optional
            LCOE value to plot, by default 'total_lcoe'
        out_path : str, optional
            File path to save plot to, by default None
        kwargs : dict
            Additional kwargs for plotting.dataframes.df_scatter
        """
        sc_df = self._extract_sc_data(lcoe=lcoe)
        mplt.df_scatter(sc_df, x='cumulative_capacity', y=lcoe,
                        filename=out_path, **kwargs)

    def supply_curve_plotly(self, lcoe='total_lcoe', out_path=None, **kwargs):
        """
        Plot supply curve (cumulative capacity vs lcoe) using plotly

        Parameters
        ----------
        lcoe : str, optional
            LCOE value to plot, by default 'total_lcoe'
        out_path : str, optional
            File path to save plot to, can be a .html or static image,
            by default None
        kwargs : dict
            Additional kwargs for plotly.express.scatter
        """
        sc_df = self._extract_sc_data(lcoe=lcoe)
        fig = px.scatter(sc_df, x='cumulative_capacity', y=lcoe, **kwargs)
        fig.update_layout(font=dict(family="Arial", size=18, color="black"))

        if out_path is not None:
            self._save_plotly(fig, out_path)

        fig.show()

    def dist_plot(self, value, out_path=None, **kwargs):
        """
        Plot distribution plot of value using seaborn.distplot

        Parameters
        ----------
        value : str
            Column name to plot
        out_path : str, optional
            File path to save plot to, by default None
        kwargs : dict
            Additional kwargs for plotting.dataframes.dist_plot
        """
        self._check_value(value, scatter=False)
        series = self.summary(value)
        mplt.dist_plot(series, filename=out_path, **kwargs)

    def dist_plotly(self, value, out_path=None, **kwargs):
        """
        Plot histogram of value using plotly

        Parameters
        ----------
        value : str
            Column name to plot
        out_path : str, optional
            File path to save plot to, by default None
        kwargs : dict
            Additional kwargs for plotly.express.histogram
        """
        self._check_value(value, scatter=False)

        fig = px.histogram(self.summary, x=value)

        if out_path is not None:
            self._save_plotly(fig, out_path, **kwargs)

        fig.show()

    @classmethod
    def scatter(cls, summary_csv, out_dir, value, type='plot', cmap='viridis',
                **kwargs):
        """
        Create scatter plot for given value in summary table and save to
        out_dir

        Parameters
        ----------
        summary_csv : str
            Path to .csv file containing summary table
        out_dir : str
            Output directory to save plots to
        value : str
            Column name to plot as color
        type : str, optional
            Type of plot to create 'plot' or 'plotly', by default 'plot'
        cmap : str, optional
            Colormap name, by default 'viridis'
        kwargs : dict
            Additional plotting kwargs
        """
        splt = cls(summary_csv)
        if type == 'plot':
            out_path = os.path.basename(summary_csv).replace('.csv', '.png')
            out_path = os.path.join(out_dir, out_path)
            splt.scatter_plot(value, cmap=cmap, out_path=out_path, **kwargs)
        elif type == 'plotly':
            out_path = os.path.basename(summary_csv).replace('.csv', '.html')
            out_path = os.path.join(out_dir, out_path)
            splt.scatter_plotly(value, cmap=cmap, out_path=out_path, **kwargs)
        else:
            msg = ("type must be 'plot' or 'plotly' but {} was given"
                   .format(type))
            logger.error(msg)
            raise ValueError(msg)

    @classmethod
    def scatter_all(cls, summary_csv, out_dir, type='plot', cmap='viridis',
                    **kwargs):
        """
        Create scatter plot for all summary stats in summary table and save to
        out_dir

        Parameters
        ----------
        summary_csv : str
            Path to .csv file containing summary table
        out_dir : str
            Output directory to save plots to
        type : str, optional
            Type of plot to create 'plot' or 'plotly', by default 'plot'
        cmap : str, optional
            Colormap name, by default 'viridis'
        kwargs : dict
            Additional plotting kwargs
        """
        splt = cls(summary_csv)
        datasets = []
        for c in splt.summary.columns:
            cols = ['mean', 'std', 'min', '25%', '50%', '75%', 'max', 'sum']
            if c.endswith('_mean') or c in cols:
                datasets.append(c)

        for value in datasets:
            if type == 'plot':
                out_path = '_{}.png'.format(value)
                out_path = \
                    os.path.basename(summary_csv).replace('.csv', out_path)
                out_path = os.path.join(out_dir, out_path)
                splt.scatter_plot(value, cmap=cmap, out_path=out_path,
                                  **kwargs)
            elif type == 'plotly':
                out_path = '_{}.html'.format(value)
                out_path = \
                    os.path.basename(summary_csv).replace('.csv', out_path)
                out_path = os.path.join(out_dir, out_path)
                splt.scatter_plotly(value, cmap=cmap, out_path=out_path,
                                    **kwargs)
            else:
                msg = ("type must be 'plot' or 'plotly' but {} was given"
                       .format(type))
                logger.error(msg)
                raise ValueError(msg)

    @classmethod
    def supply_curve(cls, summary_csv, out_dir, type='plot',
                     lcoe='total_lcoe', **kwargs):
        """
        Create supply curve plot from summary csv using lcoe value and save
        to out_dir

        Parameters
        ----------
        summary_csv : str
            Path to .csv file containing summary table
        out_dir : str
            Output directory to save plots to
        type : str, optional
            Type of plot to create 'plot' or 'plotly', by default 'plot'
        lcoe : str, optional
            LCOE value to plot, by default 'total_lcoe'
        kwargs : dict
            Additional plotting kwargs
        """
        splt = cls(summary_csv)
        if type == 'plot':
            out_path = os.path.basename(summary_csv).replace('.csv', '.png')
            out_path = os.path.join(out_dir, out_path)
            splt.supply_curve_plot(lcoe=lcoe, out_path=out_path, **kwargs)
        elif type == 'plotly':
            out_path = os.path.basename(summary_csv).replace('.csv', '.html')
            out_path = os.path.join(out_dir, out_path)
            splt.supply_curve_plotly(lcoe=lcoe, out_path=out_path, **kwargs)
        else:
            msg = ("type must be 'plot' or 'plotly' but {} was given"
                   .format(type))
            logger.error(msg)
            raise ValueError(msg)
