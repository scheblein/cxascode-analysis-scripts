import pandas as pd
import matplotlib.pyplot as plt

def generate_plt_by_resource_type(df,resource_type, top_n=None):
    """
    Generates a bar plot showing the distribution of resources by type.
    
    Args:
        df (pandas.DataFrame): Input dataframe containing resource data
        resource_type (str): Type of resource to filter and plot
        top_n (int or None): Number of top resource types to show.
                             If None (default), show all.
        
    Returns:
        matplotlib.pyplot: Bar plot showing resource type distribution
        
    The function:
    1. Filters dataframe for specified resource type
    2. Counts occurrences of each resource
    3. Creates bar plot with resource counts
    4. Adds count labels on top of each bar
    """
    df_refresh_start = df[df['type'] == resource_type]
    counts = df_refresh_start['resource_type'].value_counts()
    print(f'The total number of resource type: {resource_type} are:{len(counts)}')

    # Apply top_n limit if provided
    if top_n is not None:
        counts = counts.head(top_n)

    # create a bar graph
    plt.figure(figsize=(20, 6))
    ax = counts.plot(kind='bar')
    plt.title(f'Distribution of {resource_type} by Resource{" (Top " + str(top_n) + ")" if top_n else ""}')
    plt.xlabel('Resource Types')
    plt.ylabel('Count')
    plt.xticks(rotation=90)  # rotate x-axis labels for better readability

    for i, patch in enumerate(ax.patches):
        height = patch.get_height()
        plt.text(i, height + 0.4, f"{height}", ha='center', va='bottom')

    return plt

def generate_plt_by_method_url(df,method_url,top_n=None):
    """
    Generates a bar plot showing the distribution of method URLs.
    
    Args:
        df (pandas.DataFrame): Input dataframe containing method URL data
        method_url (str): Method URL to filter and plot
        top_n (int or None): Number of top URLs to show. 
                             If None (default), show all.
        
    Returns:
        matplotlib.pyplot: Bar plot showing method URL distribution
        
    The function:
    1. Gets value counts of method URLs from dataframe
    2. Creates bar plot with method URL counts
    3. Adds count labels on top of each bar
    4. Formats plot with labels, title and rotated x-axis ticks
    """
    method_url_counts = df['method_url'].value_counts()

    # If top_n is provided, slice the data
    if top_n is not None:
        method_url_counts = method_url_counts.head(top_n)

    plt.figure(figsize=(10,6))
    for i, (method_url, count) in enumerate(method_url_counts.items()):
        plt.bar(i, count)
        plt.text(i, count + 0.5, str(count), ha='center', va='bottom')
        
    plt.bar(method_url_counts.index, method_url_counts.values)
    plt.xlabel('Method URL')
    plt.ylabel('Count')
    plt.title(f"Total Count of Each Method URL{' (Top ' + str(top_n) + ')' if top_n else ''}")
    plt.xticks(rotation=90)
    plt.tight_layout()
    return plt

def generate_duration_by_resource_type(df_merged_refresh, metric="total", top_n=None):
    """
    Plot export durations per resource_type using df_merged_refresh
    (which must include 'resource_type' and 'time_diff_minutes').

    metric: "total" -> sum of minutes, "average" -> mean minutes
    top_n:  show only top N bars (by chosen metric)
    """
    if "time_diff_minutes" not in df_merged_refresh.columns:
        raise ValueError("df_merged_refresh must include 'time_diff_minutes'.")

    agg = "sum" if metric == "total" else "mean"
    title_metric = "TOTAL minutes" if metric == "total" else "AVERAGE minutes"

    summary = (
        df_merged_refresh
        .dropna(subset=["time_diff_minutes"])
        .groupby("resource_type", as_index=False)["time_diff_minutes"]
        .agg(value=agg)
        .sort_values("value", ascending=False)
    )

    if top_n is not None:
        summary = summary.head(top_n)

    plt.figure(figsize=(20, 6))
    bars = plt.bar(summary["resource_type"], summary["value"])
    plt.xticks(rotation=90)
    plt.ylabel("Minutes")
    plt.title(f"Export duration by resource_type — {title_metric}{' (Top ' + str(top_n) + ')' if top_n else ''}")
    plt.tight_layout()

    # labels
    for i, b in enumerate(bars):
        h = b.get_height()
        plt.text(i, h, f"{h:.2f}", ha="center", va="bottom")

    return plt
